#!/usr/bin/env python3
"""Fail-closed verifier for Derakane rebuild imports (contract v2)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

MANIFEST_NAME = "import-manifest.json"
DATASET_NAME = "derakane-dataset.json"
MANIFEST_CONTRACT = "frpdepot.derakane-search.import-manifest"
DATASET_CONTRACT = "frpdepot.derakane-search.dataset"
CONTRACT_VERSION = 2
RESIN_IDS = ("411", "441", "451", "455", "470", "510A/B/C", "510N", "515", "8084")
REBUILD_PIPELINE = "frpdepot.derakane-guide-rebuild"
RATING_STATES = frozenset({"blank", "nr", "ls", "value", "source_special"})
RESOLUTION_STATUSES = frozenset({"RESOLVED", "SOURCE_SPECIAL"})
SOURCE_SPECIAL_FORMS = frozenset({
    "limited_service_temperature_pair",
    "semicolon_separated_temperature_alternatives",
    "printed_single_temperature_without_unit",
    "printed_dash",
    "510_variant_specific_values",
})
SPECIAL_FORM_SYNTAX = {
    "limited_service_temperature_pair": "temperature_pair",
    "semicolon_separated_temperature_alternatives": "multiple_alternatives",
    "printed_single_temperature_without_unit": "single_temperature",
    "printed_dash": "printed_dash",
    "510_variant_specific_values": "resin_variant_values",
}
CAS_RE = re.compile(r"^[0-9]{2,7}-[0-9]{2}-[0-9]$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FOOTNOTE_RE = re.compile(r"<\s*(\d+(?:\s*,\s*\d+)*)\s*>")


class ImportContractError(ValueError):
    """A deterministic, release-blocking import contract violation."""


@dataclass(frozen=True)
class VerifiedImport:
    manifest_path: Path
    dataset_path: Path
    manifest: dict[str, Any]
    dataset: dict[str, Any]
    sha256: str
    byte_count: int


def _fail(message: str) -> None:
    raise ImportContractError(message)


def _object(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{path} must be an object")
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        _fail(f"{path} missing required field(s): {', '.join(sorted(missing))}")
    if unknown:
        _fail(f"{path} contains unknown field(s): {', '.join(sorted(unknown))}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{path} must be an array")
    return value


def _string(value: Any, path: str, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail(f"{path} must be a{'n' if not empty else ''} {'non-empty ' if not empty else ''}string")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{path} must be an integer >= {minimum}")
    return value


def _number(value: Any, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path} must be a number")
    return value


def _iso_datetime(value: Any, path: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _string(value, path)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail(f"{path} must be an ISO-8601 date-time: {exc}")
    return text


def _sha256(value: Any, path: str) -> str:
    text = _string(value, path)
    if not SHA256_RE.fullmatch(text):
        _fail(f"{path} must be a lowercase SHA-256")
    return text


def _unique(values: list[Any], path: str) -> None:
    fingerprints = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in values]
    if len(fingerprints) != len(set(fingerprints)):
        _fail(f"{path} must contain unique values")


def _valid_cas(cas: str) -> bool:
    digits, check = cas.rsplit("-", 1)
    body = digits.replace("-", "")
    return sum(int(digit) * weight for weight, digit in enumerate(reversed(body), 1)) % 10 == int(check)


def _source(value: Any, path: str) -> dict[str, Any]:
    source = _object(value, path, {
        "publisher", "title", "product_family", "edition", "document_code",
        "publication_date", "document_sha256", "page_count", "source_url",
    })
    for key in ("publisher", "title", "product_family", "edition", "document_code", "publication_date"):
        _string(source[key], f"{path}.{key}")
    _sha256(source["document_sha256"], f"{path}.document_sha256")
    _integer(source["page_count"], f"{path}.page_count", 1)
    if source["source_url"] is not None:
        _string(source["source_url"], f"{path}.source_url")
    return source


def _rebuild_provenance(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {"output_manifest_sha256", "output_manifest_bytes", "source_pdf_sha256", "artifacts"})
    _sha256(item["output_manifest_sha256"], f"{path}.output_manifest_sha256")
    _integer(item["output_manifest_bytes"], f"{path}.output_manifest_bytes", 1)
    _sha256(item["source_pdf_sha256"], f"{path}.source_pdf_sha256")
    artifacts = _array(item["artifacts"], f"{path}.artifacts")
    if not artifacts:
        _fail(f"{path}.artifacts must not be empty")
    names = []
    for index, artifact in enumerate(artifacts):
        apath = f"{path}.artifacts[{index}]"
        pin = _object(artifact, apath, {"path", "bytes", "sha256"})
        relative = _string(pin["path"], f"{apath}.path")
        if relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
            _fail(f"{apath}.path must be a safe relative rebuild path")
        _integer(pin["bytes"], f"{apath}.bytes", 1)
        _sha256(pin["sha256"], f"{apath}.sha256")
        names.append(relative)
    if len(names) != len(set(names)):
        _fail(f"{path}.artifacts paths must be unique")
    required = {
        "data/dataset_metadata.json", "data/records.jsonl", "data/cas_crosswalk.json",
        "data/footnotes.json", "data/semantics.json", "data/chemical_groups.json",
        "data/search_index.json", "validation/cas_adjudication/final_adjudication_report.json",
    }
    if not required.issubset(names):
        _fail(f"{path}.artifacts does not contain every required rebuild pin")
    return item


def _footnote_ids(value: Any, path: str, defined: set[int], *, unique: bool) -> list[int]:
    refs = _array(value, path)
    if unique:
        _unique(refs, path)
    for index, ref in enumerate(refs):
        _integer(ref, f"{path}[{index}]", 1)
        if ref not in defined:
            _fail(f"{path}[{index}] references undefined footnote {ref}")
    return refs


def _first_occurrence_unique(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _source_footnote_refs(value: str) -> list[int]:
    refs: list[int] = []
    for group in FOOTNOTE_RE.findall(value):
        refs.extend(int(item.strip()) for item in group.split(","))
    return refs


def _footnote_reference_pair(
    raw_value: Any,
    raw_path: str,
    display_value: Any,
    display_path: str,
    defined: set[int],
) -> tuple[list[int], list[int]]:
    """Validate lossless source refs plus their deterministic UI projection."""
    raw_refs = _footnote_ids(raw_value, raw_path, defined, unique=False)
    display_refs = _footnote_ids(display_value, display_path, defined, unique=True)
    expected_display = _first_occurrence_unique(raw_refs)
    if display_refs != expected_display:
        _fail(
            f"{display_path} must be the first-occurrence deduplicated projection of {raw_path}"
        )
    return raw_refs, display_refs


def _geometry(value: Any, path: str) -> None:
    geometry = _object(value, path, {"bbox"})
    bbox = _array(geometry["bbox"], f"{path}.bbox")
    if len(bbox) != 4:
        _fail(f"{path}.bbox must contain four coordinates")
    for index, coordinate in enumerate(bbox):
        _number(coordinate, f"{path}.bbox[{index}]")


def _component(value: Any, path: str) -> None:
    item = _object(value, path, {"unit", "value", "limited_service", "limited_service_prefix_raw"})
    if item["unit"] not in {"C", "F"}:
        _fail(f"{path}.unit must be C or F")
    _integer(item["value"], f"{path}.value")
    if not isinstance(item["limited_service"], bool):
        _fail(f"{path}.limited_service must be boolean")
    prefix = item["limited_service_prefix_raw"]
    if prefix is not None:
        _string(prefix, f"{path}.limited_service_prefix_raw")


def _alternative(value: Any, path: str) -> None:
    if not isinstance(value, dict) or "kind" not in value:
        _fail(f"{path} must be a closed rating alternative")
    kind = value["kind"]
    if kind == "temperature_pair":
        item = _object(value, path, {"kind", "source_raw", "components", "temperature_pair_order"})
        if item["temperature_pair_order"] != "C/F":
            _fail(f"{path}.temperature_pair_order must be C/F")
        components = _array(item["components"], f"{path}.components")
        if len(components) != 2:
            _fail(f"{path}.components must contain C and F")
        for index, component in enumerate(components):
            _component(component, f"{path}.components[{index}]")
        if [component["unit"] for component in components] != ["C", "F"]:
            _fail(f"{path}.components must preserve C/F order")
    elif kind == "single_temperature":
        item = _object(value, path, {"kind", "source_raw", "value", "unit", "unit_status"})
        _integer(item["value"], f"{path}.value")
        if item["unit"] is not None or item["unit_status"] != "NOT_PRINTED":
            _fail(f"{path} single source temperature must preserve absent unit")
    elif kind == "printed_dash":
        item = _object(value, path, {"kind", "source_raw"})
        if item["source_raw"] != "-":
            _fail(f"{path}.source_raw must be the printed dash")
    else:
        _fail(f"{path}.kind is unsupported")
    _string(item["source_raw"], f"{path}.source_raw")


def _variant_rating(value: Any, path: str) -> None:
    if isinstance(value, dict) and value.get("state") == "not_printed":
        item = _object(value, path, {"state", "source_raw"})
        if item["source_raw"] is not None:
            _fail(f"{path}.source_raw must be null when a variant was not printed")
    else:
        _alternative(value, path)


def _resin_variants(value: Any, path: str) -> None:
    item = _object(value, path, {"A", "B", "C", "assignments", "source_notation", "source_notation_raw", "unassigned_variants"})
    for variant in ("A", "B", "C"):
        _variant_rating(item[variant], f"{path}.{variant}")
    assignments = _array(item["assignments"], f"{path}.assignments")
    for index, assignment in enumerate(assignments):
        apath = f"{path}.assignments[{index}]"
        parsed = _object(assignment, apath, {"label_raw", "rating", "variants"})
        _string(parsed["label_raw"], f"{apath}.label_raw")
        _alternative(parsed["rating"], f"{apath}.rating")
        variants = _array(parsed["variants"], f"{apath}.variants")
        if not variants or any(variant not in {"A", "B", "C"} for variant in variants):
            _fail(f"{apath}.variants is invalid")
    _string(item["source_notation"], f"{path}.source_notation")
    _string(item["source_notation_raw"], f"{path}.source_notation_raw")
    unassigned = _array(item["unassigned_variants"], f"{path}.unassigned_variants")
    if any(variant not in {"A", "B", "C"} for variant in unassigned):
        _fail(f"{path}.unassigned_variants is invalid")


def _rating(value: Any, path: str, resin_id: str, resin_sequence: int, defined: set[int]) -> dict[str, Any]:
    item = _object(value, path, {
        "alternative_separators_raw", "alternatives", "footnote_refs_raw", "footnote_refs", "rating_model_version",
        "resin", "resin_sequence", "resolution_status", "source_geometry", "source_raw", "state",
        "syntax", "temperature_c", "temperature_f", "temperature_pair_order", "value_text",
    }, {"source_special_form", "resin_variants"})
    if item["resin"] != resin_id or item["resin_sequence"] != resin_sequence:
        _fail(f"{path} breaks source resin order")
    if item["rating_model_version"] != 2:
        _fail(f"{path}.rating_model_version must be 2")
    state = item["state"]
    if state not in RATING_STATES:
        _fail(f"{path}.state is unsupported or unresolved")
    if item["resolution_status"] not in RESOLUTION_STATUSES:
        _fail(f"{path}.resolution_status is unsupported or unresolved")
    _string(item["source_raw"], f"{path}.source_raw", empty=True)
    _string(item["value_text"], f"{path}.value_text", empty=True)
    _geometry(item["source_geometry"], f"{path}.source_geometry")
    raw_refs, _ = _footnote_reference_pair(
        item["footnote_refs_raw"], f"{path}.footnote_refs_raw",
        item["footnote_refs"], f"{path}.footnote_refs", defined,
    )
    if raw_refs != _source_footnote_refs(item["source_raw"]):
        _fail(f"{path}.footnote_refs_raw must exactly match references in source_raw")
    separators = _array(item["alternative_separators_raw"], f"{path}.alternative_separators_raw")
    for index, separator in enumerate(separators):
        _string(separator, f"{path}.alternative_separators_raw[{index}]")
    alternatives = _array(item["alternatives"], f"{path}.alternatives")
    for index, alternative in enumerate(alternatives):
        _alternative(alternative, f"{path}.alternatives[{index}]")

    temperatures = (item["temperature_c"], item["temperature_f"])
    if state == "value":
        if item["resolution_status"] != "RESOLVED" or item["syntax"] != "temperature_pair":
            _fail(f"{path} value rating has invalid resolution/syntax")
        for index, temperature in enumerate(temperatures):
            _integer(temperature, f"{path}.temperature_{'c' if index == 0 else 'f'}")
        if item["temperature_pair_order"] != "C/F" or "source_special_form" in item or "resin_variants" in item:
            _fail(f"{path} value rating has invalid unit/special metadata")
    elif state in {"blank", "nr", "ls"}:
        expected_syntax = {"blank": "blank", "nr": "not_recommended", "ls": "limited_service"}[state]
        if item["resolution_status"] != "RESOLVED" or item["syntax"] != expected_syntax:
            _fail(f"{path} {state} rating has invalid resolution/syntax")
        if temperatures != (None, None) or item["temperature_pair_order"] is not None:
            _fail(f"{path} non-value rating must have null temperatures/units")
        if "source_special_form" in item or "resin_variants" in item:
            _fail(f"{path} ordinary state cannot carry source-special metadata")
    else:
        form = item.get("source_special_form")
        if form not in SOURCE_SPECIAL_FORMS or item["resolution_status"] != "SOURCE_SPECIAL":
            _fail(f"{path} source_special must carry a supported closed form")
        if item["syntax"] != SPECIAL_FORM_SYNTAX[form]:
            _fail(f"{path} source_special form/syntax mismatch")
        if form in {"semicolon_separated_temperature_alternatives", "printed_single_temperature_without_unit", "printed_dash", "510_variant_specific_values"}:
            if temperatures != (None, None) or item["temperature_pair_order"] is not None:
                _fail(f"{path} special form must not infer a C/F pair")
        if form == "510_variant_specific_values":
            if "resin_variants" not in item or resin_id != "510A/B/C":
                _fail(f"{path} 510 variant form must carry complete variant assignments")
            _resin_variants(item["resin_variants"], f"{path}.resin_variants")
        elif "resin_variants" in item:
            _fail(f"{path} non-variant form cannot carry resin_variants")
    return item


def _adjudication(value: Any, path: str, searchable: bool) -> None:
    item = _object(value, path, {"category", "category_label", "mechanism", "rationale", "status"})
    for key in ("category", "category_label", "mechanism", "rationale", "status"):
        _string(item[key], f"{path}.{key}")
    if searchable and item["status"] != "PROVEN":
        _fail(f"{path}.status must be PROVEN for public CAS")
    if not searchable and (item["category"] != "c" or item["status"] != "EXCLUDED"):
        _fail(f"{path} excluded CAS must have category c / EXCLUDED adjudication")


def _cas_entry(value: Any, path: str, source_page_count: int, source_pdf_sha256: str) -> dict[str, Any]:
    item = _object(value, path, {
        "cas_entry_id", "source_sequence", "cas_raw", "raw_pdf_cas", "normalized_cas",
        "public_searchable", "checksum_valid", "chemical_name_display", "chemical_name_raw",
        "chemical_search_key", "adjudication", "authority_provenance", "source",
    })
    _string(item["cas_entry_id"], f"{path}.cas_entry_id")
    _integer(item["source_sequence"], f"{path}.source_sequence", 1)
    _string(item["cas_raw"], f"{path}.cas_raw")
    _string(item["raw_pdf_cas"], f"{path}.raw_pdf_cas")
    if item["cas_raw"] != item["raw_pdf_cas"]:
        _fail(f"{path} CAS raw forms must agree")
    if not isinstance(item["public_searchable"], bool) or not isinstance(item["checksum_valid"], bool):
        _fail(f"{path} CAS flags must be boolean")
    if item["public_searchable"]:
        normalized = _string(item["normalized_cas"], f"{path}.normalized_cas")
        if not CAS_RE.fullmatch(normalized) or not _valid_cas(normalized):
            _fail(f"{path}.normalized_cas must be checksum-valid for public search")
    elif item["normalized_cas"] is not None:
        _fail(f"{path}.normalized_cas must be null when excluded from public CAS search")
    for key in ("chemical_name_display", "chemical_name_raw", "chemical_search_key"):
        _string(item[key], f"{path}.{key}")
    _adjudication(item["adjudication"], f"{path}.adjudication", item["public_searchable"])
    provenance = _array(item["authority_provenance"], f"{path}.authority_provenance")
    for index, evidence in enumerate(provenance):
        epath = f"{path}.authority_provenance[{index}]"
        proof = _object(evidence, epath, {
            "body_sha256", "evidence_path", "http_status", "official_source",
            "query", "retrieved_utc", "role", "url",
        }, optional={"pubchem_cids", "pubchem_sids"})
        _sha256(proof["body_sha256"], f"{epath}.body_sha256")
        for key in ("evidence_path", "official_source", "query", "role", "url"):
            _string(proof[key], f"{epath}.{key}")
        _integer(proof["http_status"], f"{epath}.http_status", 100)
        _iso_datetime(proof["retrieved_utc"], f"{epath}.retrieved_utc")
        if ("pubchem_cids" in proof) == ("pubchem_sids" in proof):
            _fail(f"{epath} must retain exactly one PubChem CID or SID list")
        if "pubchem_cids" in proof:
            for cid_index, cid in enumerate(_array(proof["pubchem_cids"], f"{epath}.pubchem_cids")):
                _integer(cid, f"{epath}.pubchem_cids[{cid_index}]", 1)
        if "pubchem_sids" in proof:
            for sid_index, sid in enumerate(_array(proof["pubchem_sids"], f"{epath}.pubchem_sids")):
                _integer(sid, f"{epath}.pubchem_sids[{sid_index}]", 1)
    source = _object(item["source"], f"{path}.source", {"column", "line_bboxes", "page", "pdf_sha256", "raw_lines"})
    _integer(source["column"], f"{path}.source.column", 1)
    page = _integer(source["page"], f"{path}.source.page", 1)
    if page > source_page_count:
        _fail(f"{path}.source.page exceeds source page count")
    _sha256(source["pdf_sha256"], f"{path}.source.pdf_sha256")
    if source["pdf_sha256"] != source_pdf_sha256:
        _fail(f"{path}.source.pdf_sha256 does not match the dataset source PDF")
    bboxes = _array(source["line_bboxes"], f"{path}.source.line_bboxes")
    lines = _array(source["raw_lines"], f"{path}.source.raw_lines")
    if not bboxes or len(bboxes) != len(lines):
        _fail(f"{path}.source line geometry/text counts must agree")
    for index, bbox in enumerate(bboxes):
        if len(_array(bbox, f"{path}.source.line_bboxes[{index}]")) != 4:
            _fail(f"{path}.source.line_bboxes[{index}] must contain four coordinates")
        for cindex, coordinate in enumerate(bbox):
            _number(coordinate, f"{path}.source.line_bboxes[{index}][{cindex}]")
        _string(lines[index], f"{path}.source.raw_lines[{index}]")
    if item["raw_pdf_cas"] not in "\n".join(lines):
        _fail(f"{path}.raw_pdf_cas is missing from retained source lines")
    return item


def _semantics(value: Any, path: str) -> dict[str, Any]:
    item = _object(value, path, {
        "blank", "concentration", "ls", "model_states", "nr", "parser_uncertainty",
        "rating_model_version", "resolution_statuses", "source_page", "source_special", "temperature_pair",
    })
    for name, state in (("blank", "blank"), ("nr", "nr"), ("ls", "ls")):
        parsed = _object(item[name], f"{path}.{name}", {"source_quote", "state"})
        _string(parsed["source_quote"], f"{path}.{name}.source_quote")
        if parsed["state"] != state:
            _fail(f"{path}.{name}.state must be {state}")
    concentration = _object(item["concentration"], f"{path}.concentration", {"column_header_raw", "legend"})
    _string(concentration["column_header_raw"], f"{path}.concentration.column_header_raw")
    _string(concentration["legend"], f"{path}.concentration.legend")
    if item["model_states"] != ["blank", "nr", "ls", "value", "source_special", "parser_uncertainty"]:
        _fail(f"{path}.model_states must be the exact closed rating state list")
    uncertainty = _object(item["parser_uncertainty"], f"{path}.parser_uncertainty", {"meaning", "resolution_status", "state"})
    _string(uncertainty["meaning"], f"{path}.parser_uncertainty.meaning")
    if uncertainty["resolution_status"] != "PARSER_UNCERTAINTY" or uncertainty["state"] != "parser_uncertainty":
        _fail(f"{path}.parser_uncertainty is malformed")
    if item["rating_model_version"] != 2:
        _fail(f"{path}.rating_model_version must be 2")
    if item["resolution_statuses"] != ["RESOLVED", "SOURCE_SPECIAL", "PARSER_UNCERTAINTY"]:
        _fail(f"{path}.resolution_statuses must be the exact closed list")
    _integer(item["source_page"], f"{path}.source_page", 1)
    special = _object(item["source_special"], f"{path}.source_special", {"forms", "meaning", "resolution_status", "state"})
    forms = _array(special["forms"], f"{path}.source_special.forms")
    if len(forms) != len(SOURCE_SPECIAL_FORMS) or set(forms) != SOURCE_SPECIAL_FORMS:
        _fail(f"{path}.source_special.forms are incomplete")
    _string(special["meaning"], f"{path}.source_special.meaning")
    if special["resolution_status"] != "SOURCE_SPECIAL" or special["state"] != "source_special":
        _fail(f"{path}.source_special is malformed")
    pair = _object(item["temperature_pair"], f"{path}.temperature_pair", {
        "column_header_raw", "meaning", "not_necessarily_maximum_service_temperature",
        "page_text_sha256", "source_quote_parts",
    })
    if pair["column_header_raw"] != "°C/°F" or pair["not_necessarily_maximum_service_temperature"] is not True:
        _fail(f"{path}.temperature_pair must preserve printed C/F semantics")
    _string(pair["meaning"], f"{path}.temperature_pair.meaning")
    _sha256(pair["page_text_sha256"], f"{path}.temperature_pair.page_text_sha256")
    quotes = _array(pair["source_quote_parts"], f"{path}.temperature_pair.source_quote_parts")
    if not quotes:
        _fail(f"{path}.temperature_pair.source_quote_parts must not be empty")
    for index, quote in enumerate(quotes):
        _string(quote, f"{path}.temperature_pair.source_quote_parts[{index}]")
    return item


def validate_manifest(manifest: Any) -> dict[str, Any]:
    value = _object(manifest, "manifest", {
        "contract", "contract_version", "generated_utc", "producer", "verification", "source",
        "rebuild_provenance", "dataset", "summary",
    }, {"permission_documented"})
    if value["contract"] != MANIFEST_CONTRACT or value["contract_version"] != CONTRACT_VERSION:
        _fail("manifest contract identifier/version is not supported")
    _iso_datetime(value["generated_utc"], "manifest.generated_utc")
    if "permission_documented" in value and not isinstance(value["permission_documented"], bool):
        _fail("manifest.permission_documented must be boolean when present")
    producer = _object(value["producer"], "manifest.producer", {"pipeline", "contract_version", "run_id"})
    if producer["pipeline"] != REBUILD_PIPELINE or producer["contract_version"] != CONTRACT_VERSION:
        _fail("manifest producer is not the supported rebuild pipeline contract")
    _string(producer["run_id"], "manifest.producer.run_id")
    verification = _object(value["verification"], "manifest.verification", {
        "status", "unresolved_count", "reviewed_row_count", "method", "verified_utc", "verifier",
    })
    if verification["status"] not in {"VERIFIED", "NOT_VERIFIED"}:
        _fail("manifest.verification.status must be VERIFIED or NOT_VERIFIED")
    _integer(verification["unresolved_count"], "manifest.verification.unresolved_count")
    _integer(verification["reviewed_row_count"], "manifest.verification.reviewed_row_count")
    _string(verification["method"], "manifest.verification.method")
    _iso_datetime(verification["verified_utc"], "manifest.verification.verified_utc", nullable=True)
    if verification["verifier"] is not None:
        _string(verification["verifier"], "manifest.verification.verifier")
    source = _source(value["source"], "manifest.source")
    provenance = _rebuild_provenance(value["rebuild_provenance"], "manifest.rebuild_provenance")
    if provenance["source_pdf_sha256"] != source["document_sha256"]:
        _fail("manifest rebuild/source PDF hashes do not agree")
    pin = _object(value["dataset"], "manifest.dataset", {"file", "bytes", "sha256"})
    if pin["file"] != DATASET_NAME:
        _fail(f"manifest.dataset.file must be exactly {DATASET_NAME}")
    _integer(pin["bytes"], "manifest.dataset.bytes", 1)
    _sha256(pin["sha256"], "manifest.dataset.sha256")
    summary = _object(value["summary"], "manifest.summary", {
        "row_count", "chemical_count", "search_entity_count", "footnote_count", "resin_columns",
        "rating_count", "source_special_count", "parser_uncertainty_count", "cas_entry_count",
        "cas_public_searchable_count", "cas_excluded_count",
    })
    for key in ("row_count", "chemical_count", "search_entity_count", "footnote_count", "rating_count", "source_special_count", "parser_uncertainty_count", "cas_entry_count", "cas_public_searchable_count", "cas_excluded_count"):
        _integer(summary[key], f"manifest.summary.{key}", 0 if "count" in key else 1)
    if summary["footnote_count"] != 25:
        _fail("manifest.summary.footnote_count must be exactly 25")
    if summary["parser_uncertainty_count"] != 0:
        _fail("manifest.summary.parser_uncertainty_count must be zero")
    if tuple(_array(summary["resin_columns"], "manifest.summary.resin_columns")) != RESIN_IDS:
        _fail("manifest.summary.resin_columns must be the exact ordered nine-column list")
    return value


def validate_dataset(dataset: Any) -> dict[str, Any]:
    value = _object(dataset, "dataset", {
        "contract", "contract_version", "source", "rebuild_provenance", "resin_columns", "footnotes",
        "semantics", "cas_catalog", "search_entities", "rows",
    })
    if value["contract"] != DATASET_CONTRACT or value["contract_version"] != CONTRACT_VERSION:
        _fail("dataset contract identifier/version is not supported")
    source = _source(value["source"], "dataset.source")
    provenance = _rebuild_provenance(value["rebuild_provenance"], "dataset.rebuild_provenance")
    if provenance["source_pdf_sha256"] != source["document_sha256"]:
        _fail("dataset rebuild/source PDF hashes do not agree")
    columns = _array(value["resin_columns"], "dataset.resin_columns")
    if len(columns) != 9:
        _fail("dataset.resin_columns must contain exactly nine columns")
    for index, (column, expected_id) in enumerate(zip(columns, RESIN_IDS), 1):
        item = _object(column, f"dataset.resin_columns[{index - 1}]", {"id", "label", "source_sequence"})
        if item["id"] != expected_id or item["source_sequence"] != index:
            _fail("dataset.resin_columns must preserve the exact source column order")
        _string(item["label"], f"dataset.resin_columns[{index - 1}].label")

    footnotes = _array(value["footnotes"], "dataset.footnotes")
    if len(footnotes) != 25:
        _fail("dataset.footnotes must contain exactly PDF-proven definitions 1 through 25")
    defined: set[int] = set()
    for index, footnote in enumerate(footnotes, 1):
        fpath = f"dataset.footnotes[{index - 1}]"
        item = _object(footnote, fpath, {"id", "text", "source_page", "source_parts"})
        if item["id"] != index:
            _fail("dataset.footnotes must be ordered definitions 1 through 25")
        _string(item["text"], f"{fpath}.text")
        page = _integer(item["source_page"], f"{fpath}.source_page", 1)
        if page > source["page_count"]:
            _fail(f"{fpath}.source_page exceeds source page count")
        parts = _array(item["source_parts"], f"{fpath}.source_parts")
        if not parts:
            _fail(f"{fpath}.source_parts must not be empty")
        for pindex, part in enumerate(parts):
            ppath = f"{fpath}.source_parts[{pindex}]"
            parsed = _object(part, ppath, {"bbox", "raw"})
            bbox = _array(parsed["bbox"], f"{ppath}.bbox")
            if len(bbox) != 4:
                _fail(f"{ppath}.bbox must contain four coordinates")
            _string(parsed["raw"], f"{ppath}.raw")
        defined.add(index)

    _semantics(value["semantics"], "dataset.semantics")

    cas_catalog = _array(value["cas_catalog"], "dataset.cas_catalog")
    if not cas_catalog:
        _fail("dataset.cas_catalog must not be empty")
    cas_by_id: dict[str, dict[str, Any]] = {}
    cas_sequences = []
    for index, entry in enumerate(cas_catalog):
        parsed = _cas_entry(entry, f"dataset.cas_catalog[{index}]", source["page_count"], source["document_sha256"])
        entry_id = parsed["cas_entry_id"]
        if entry_id in cas_by_id:
            _fail(f"dataset.cas_catalog[{index}].cas_entry_id is duplicated")
        cas_by_id[entry_id] = parsed
        cas_sequences.append(parsed["source_sequence"])
    if cas_sequences != sorted(cas_sequences) or len(cas_sequences) != len(set(cas_sequences)):
        _fail("dataset.cas_catalog must preserve unique increasing source sequence")

    entities = _array(value["search_entities"], "dataset.search_entities")
    entity_by_id: dict[str, dict[str, Any]] = {}
    record_entity: dict[str, str] = {}
    entity_orders = []
    for index, entity in enumerate(entities):
        epath = f"dataset.search_entities[{index}]"
        item = _object(entity, epath, {
            "entity_type", "entity_id", "source_order", "display_name", "name_key", "aliases",
            "cas_entry_ids", "record_ids", "concentrations_in_source_order",
        })
        if item["entity_type"] not in {"table_chemical", "cas_catalog_only"}:
            _fail(f"{epath}.entity_type is unsupported")
        entity_id = _string(item["entity_id"], f"{epath}.entity_id")
        if entity_id in entity_by_id:
            _fail(f"{epath}.entity_id is duplicated")
        entity_by_id[entity_id] = item
        entity_orders.append(_integer(item["source_order"], f"{epath}.source_order", 1))
        _string(item["display_name"], f"{epath}.display_name")
        _string(item["name_key"], f"{epath}.name_key")
        aliases = _array(item["aliases"], f"{epath}.aliases")
        for aindex, alias in enumerate(aliases):
            apath = f"{epath}.aliases[{aindex}]"
            parsed_alias = _object(alias, apath, {"display", "search_key", "basis"})
            for key in ("display", "search_key", "basis"):
                _string(parsed_alias[key], f"{apath}.{key}")
        cas_ids = _array(item["cas_entry_ids"], f"{epath}.cas_entry_ids")
        _unique(cas_ids, f"{epath}.cas_entry_ids")
        for cindex, entry_id in enumerate(cas_ids):
            if entry_id not in cas_by_id:
                _fail(f"{epath}.cas_entry_ids[{cindex}] is undefined")
        linked_public = [
            cas_by_id[entry_id]["normalized_cas"]
            for entry_id in cas_ids
            if cas_by_id[entry_id]["public_searchable"]
        ]
        excluded_raw_keys = {
            re.sub(r"[^a-z0-9]+", " ", cas_by_id[entry_id]["raw_pdf_cas"].lower()).strip()
            for entry_id in cas_ids
            if not cas_by_id[entry_id]["public_searchable"]
        }
        public_search_texts = [item["name_key"], *[alias["search_key"] for alias in aliases], *linked_public]
        if any(raw_key and raw_key in public_search_texts for raw_key in excluded_raw_keys):
            _fail(f"{epath} leaks an excluded raw CAS into public search metadata")
        record_ids = _array(item["record_ids"], f"{epath}.record_ids")
        _unique(record_ids, f"{epath}.record_ids")
        if item["entity_type"] == "table_chemical" and not record_ids:
            _fail(f"{epath}.record_ids must not be empty for a table chemical")
        if item["entity_type"] == "cas_catalog_only" and record_ids:
            _fail(f"{epath}.record_ids must be empty for a CAS-catalog-only entity")
        for record_id in record_ids:
            _string(record_id, f"{epath}.record_ids")
            if record_id in record_entity:
                _fail(f"record {record_id} is assigned to multiple search entities")
            record_entity[record_id] = entity_id
        concentrations = _array(item["concentrations_in_source_order"], f"{epath}.concentrations_in_source_order")
        if len(concentrations) != len(record_ids):
            _fail(f"{epath}.concentrations_in_source_order must align with record_ids")
        for cindex, concentration in enumerate(concentrations):
            cpath = f"{epath}.concentrations_in_source_order[{cindex}]"
            parsed = _object(concentration, cpath, {"display", "record_id", "source_page", "source_raw", "source_sequence"})
            _string(parsed["display"], f"{cpath}.display", empty=True)
            _string(parsed["source_raw"], f"{cpath}.source_raw", empty=True)
            if parsed["record_id"] != record_ids[cindex]:
                _fail(f"{cpath}.record_id breaks source concentration order")
    if entity_orders != sorted(entity_orders) or len(entity_orders) != len(set(entity_orders)):
        _fail("dataset.search_entities must preserve unique increasing source order")

    rows = _array(value["rows"], "dataset.rows")
    if not rows:
        _fail("dataset.rows must not be empty")
    row_ids: set[str] = set()
    row_sequences = []
    source_special_forms: set[str] = set()
    for index, row in enumerate(rows):
        path = f"dataset.rows[{index}]"
        item = _object(row, path, {
            "row_id", "source_sequence", "source_page", "chemical_sequence", "chemical_id", "chemical_name",
            "chemical_source", "aliases", "cas_entry_ids", "public_cas_numbers", "concentration",
            "row_footnote_ids_raw", "row_footnote_ids", "cells", "source", "qa_status",
        })
        row_id = _string(item["row_id"], f"{path}.row_id")
        if row_id in row_ids:
            _fail(f"{path}.row_id is duplicated")
        row_ids.add(row_id)
        row_sequences.append(_integer(item["source_sequence"], f"{path}.source_sequence", 1))
        page = _integer(item["source_page"], f"{path}.source_page", 1)
        if page > source["page_count"]:
            _fail(f"{path}.source_page exceeds source page count")
        _integer(item["chemical_sequence"], f"{path}.chemical_sequence", 1)
        entity_id = _string(item["chemical_id"], f"{path}.chemical_id")
        if record_entity.get(row_id) != entity_id:
            _fail(f"{path} is not assigned to its search entity")
        entity = entity_by_id[entity_id]
        if item["chemical_name"] != entity["display_name"] or item["aliases"] != entity["aliases"] or item["cas_entry_ids"] != entity["cas_entry_ids"]:
            _fail(f"{path} chemical search metadata differs from its entity")
        chemical_source = _object(item["chemical_source"], f"{path}.chemical_source", {"display", "footnote_refs_raw", "footnote_refs", "search_key", "source_raw"})
        for key in ("display", "search_key", "source_raw"):
            _string(chemical_source[key], f"{path}.chemical_source.{key}")
        chemical_refs_raw, _ = _footnote_reference_pair(
            chemical_source["footnote_refs_raw"], f"{path}.chemical_source.footnote_refs_raw",
            chemical_source["footnote_refs"], f"{path}.chemical_source.footnote_refs", defined,
        )
        if chemical_refs_raw != _source_footnote_refs(chemical_source["source_raw"]):
            _fail(f"{path}.chemical_source.footnote_refs_raw must exactly match references in source_raw")
        public_cas = _array(item["public_cas_numbers"], f"{path}.public_cas_numbers")
        _unique(public_cas, f"{path}.public_cas_numbers")
        expected_public = []
        for entry_id in item["cas_entry_ids"]:
            entry = cas_by_id[entry_id]
            if entry["public_searchable"] and entry["normalized_cas"] not in expected_public:
                expected_public.append(entry["normalized_cas"])
        if public_cas != expected_public:
            _fail(f"{path}.public_cas_numbers must contain only linked normalized public CAS")
        concentration = _object(item["concentration"], f"{path}.concentration", {"column_unit_raw", "display", "source_raw", "unit_semantics"})
        for key in ("column_unit_raw", "unit_semantics"):
            _string(concentration[key], f"{path}.concentration.{key}")
        for key in ("display", "source_raw"):
            _string(concentration[key], f"{path}.concentration.{key}", empty=True)
        row_refs_raw, _ = _footnote_reference_pair(
            item["row_footnote_ids_raw"], f"{path}.row_footnote_ids_raw",
            item["row_footnote_ids"], f"{path}.row_footnote_ids", defined,
        )
        cells = _array(item["cells"], f"{path}.cells")
        if len(cells) != 9:
            _fail(f"{path}.cells must contain exactly nine cells")
        for cindex, (cell, resin_id) in enumerate(zip(cells, RESIN_IDS), 1):
            cpath = f"{path}.cells[{cindex - 1}]"
            parsed = _object(cell, cpath, {"resin_id", "raw", "footnote_ids_raw", "footnote_ids", "ratings"})
            if parsed["resin_id"] != resin_id:
                _fail(f"{cpath}.resin_id breaks source column order")
            _string(parsed["raw"], f"{cpath}.raw", empty=True)
            _footnote_reference_pair(
                parsed["footnote_ids_raw"], f"{cpath}.footnote_ids_raw",
                parsed["footnote_ids"], f"{cpath}.footnote_ids", defined,
            )
            ratings = _array(parsed["ratings"], f"{cpath}.ratings")
            if len(ratings) != 1:
                _fail(f"{cpath}.ratings must contain the one lossless source rating")
            rating = _rating(ratings[0], f"{cpath}.ratings[0]", resin_id, cindex, defined)
            if (
                parsed["raw"] != rating["source_raw"]
                or parsed["footnote_ids_raw"] != rating["footnote_refs_raw"]
                or parsed["footnote_ids"] != rating["footnote_refs"]
            ):
                _fail(f"{cpath} raw text/footnotes differ from its source rating")
            if rating["state"] == "source_special":
                source_special_forms.add(rating["source_special_form"])
        component_refs = sorted(
            chemical_refs_raw
            + _source_footnote_refs(concentration["source_raw"])
            + [ref for cell in cells for ref in cell["footnote_ids_raw"]]
        )
        if row_refs_raw != component_refs:
            _fail(f"{path}.row_footnote_ids_raw must exactly preserve all source component references")
        source_record = _object(item["source"], f"{path}.source", {
            "cell_bboxes", "page", "page_row_index", "pdf_sha256", "raw_cells", "row_bbox", "table_index",
        })
        if source_record["page"] != page or source_record["pdf_sha256"] != source["document_sha256"]:
            _fail(f"{path}.source provenance does not match row/source PDF")
        raw_cells = _array(source_record["raw_cells"], f"{path}.source.raw_cells")
        bboxes = _array(source_record["cell_bboxes"], f"{path}.source.cell_bboxes")
        if len(raw_cells) != 11 or len(bboxes) != 11:
            _fail(f"{path}.source must preserve all eleven source table cells (name, concentration, nine ratings)")
        if raw_cells[0] != item["chemical_source"]["source_raw"] or raw_cells[1] != concentration["source_raw"] or raw_cells[2:] != [cell["raw"] for cell in cells]:
            _fail(f"{path}.source.raw_cells does not round-trip row content")
        if item["qa_status"] != "VERIFIED":
            _fail(f"{path}.qa_status must be VERIFIED")
    if row_sequences != sorted(row_sequences) or len(row_sequences) != len(set(row_sequences)):
        _fail("dataset.rows must have unique, strictly increasing source_sequence values")
    if set(record_entity) != row_ids:
        _fail("dataset search entities and rows must have exact record coverage")
    if source_special_forms != SOURCE_SPECIAL_FORMS:
        _fail("dataset rows must exercise every closed source-special form")
    return value


def verify_import(data_dir: Path | str) -> VerifiedImport:
    directory = Path(data_dir)
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        _fail(f"verified dataset manifest is missing: {manifest_path}")
    if manifest_path.is_symlink():
        _fail("dataset manifest may not be a symlink")
    try:
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read dataset manifest: {exc}")
    verification = manifest["verification"]
    if verification["status"] != "VERIFIED":
        _fail("dataset manifest verification status is NOT_VERIFIED")
    if verification["unresolved_count"] != 0:
        _fail("dataset manifest contains unresolved records")
    if verification["verified_utc"] is None or verification["verifier"] is None:
        _fail("verified dataset manifest must name verifier and verification time")
    dataset_path = directory / manifest["dataset"]["file"]
    if dataset_path.parent.resolve() != directory.resolve() or dataset_path.name != DATASET_NAME:
        _fail("dataset path escapes the fixed import directory")
    if not dataset_path.is_file():
        _fail(f"manifest-pinned dataset is missing: {dataset_path}")
    if dataset_path.is_symlink():
        _fail("dataset file may not be a symlink")
    payload = dataset_path.read_bytes()
    actual_hash = hashlib.sha256(payload).hexdigest()
    actual_bytes = len(payload)
    if actual_bytes != manifest["dataset"]["bytes"]:
        _fail("manifest-pinned dataset byte count mismatch")
    if actual_hash != manifest["dataset"]["sha256"]:
        _fail("manifest-pinned dataset SHA-256 mismatch")
    try:
        dataset = validate_dataset(json.loads(payload.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot decode manifest-pinned dataset: {exc}")
    if dataset["source"] != manifest["source"] or dataset["rebuild_provenance"] != manifest["rebuild_provenance"]:
        _fail("manifest source/rebuild provenance does not exactly match dataset")
    rows = dataset["rows"]
    summary = manifest["summary"]
    facts = {
        "row_count": len(rows),
        "chemical_count": len({row["chemical_id"] for row in rows}),
        "search_entity_count": len(dataset["search_entities"]),
        "footnote_count": len(dataset["footnotes"]),
        "rating_count": sum(len(cell["ratings"]) for row in rows for cell in row["cells"]),
        "source_special_count": sum(rating["state"] == "source_special" for row in rows for cell in row["cells"] for rating in cell["ratings"]),
        "parser_uncertainty_count": 0,
        "cas_entry_count": len(dataset["cas_catalog"]),
        "cas_public_searchable_count": sum(entry["public_searchable"] for entry in dataset["cas_catalog"]),
        "cas_excluded_count": sum(not entry["public_searchable"] for entry in dataset["cas_catalog"]),
    }
    if verification["reviewed_row_count"] != len(rows):
        _fail("manifest reviewed row count does not match dataset")
    for key, actual in facts.items():
        if summary[key] != actual:
            _fail(f"manifest summary {key.replace('_', ' ')} does not match dataset")
    if tuple(column["id"] for column in dataset["resin_columns"]) != tuple(summary["resin_columns"]):
        _fail("manifest summary resin columns do not match dataset")
    return VerifiedImport(manifest_path, dataset_path, manifest, dataset, actual_hash, actual_bytes)
