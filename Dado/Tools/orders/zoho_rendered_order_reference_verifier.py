#!/usr/bin/env python
"""Read-only API + rendered-PDF verifier for one FRP Depot Zoho order document.

No business write verb and no email path exists in this module. The OAuth token
refresh is delegated to the commissioned connection helper; document reads are
GET-only and bounded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ZOHO_DIR = Path(__file__).resolve().parents[1] / "zoho"
if str(ZOHO_DIR) not in sys.path:
    sys.path.insert(0, str(ZOHO_DIR))

import zoho_tool  # noqa: E402

MAX_PDF_BYTES = 8 * 1024 * 1024
KINDS = {
    "quote": {"segment": "estimates", "key": "estimate", "number_field": "estimate_number", "label": "Reference#"},
    "sales_order": {"segment": "salesorders", "key": "salesorder", "number_field": "salesorder_number", "label": "Ref#"},
    "invoice": {"segment": "invoices", "key": "invoice", "number_field": "invoice_number", "label": "P.O.#"},
}
ZOHO_ID_RE = re.compile(r"^[1-9][0-9]*$")
INTERNAL_DOCUMENT_RE = re.compile(r"^(?:QT|SO|INV)-\d+$", re.IGNORECASE)


class RenderedOrderVerificationError(RuntimeError):
    pass


def clean_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise RenderedOrderVerificationError(f"{label} must be text.")
    if value != value.strip() or not value or "\x00" in value or "\n" in value or len(value) > limit:
        raise RenderedOrderVerificationError(f"{label} must be nonblank, trimmed single-line text <= {limit} characters.")
    return value


def validate_args(args: argparse.Namespace) -> dict[str, Any]:
    kind = clean_text(args.kind, "kind", 20)
    if kind not in KINDS:
        raise RenderedOrderVerificationError("kind must be quote, sales_order, or invoice.")
    record_id = clean_text(args.record_id, "record_id", 30)
    if not ZOHO_ID_RE.fullmatch(record_id):
        raise RenderedOrderVerificationError("record_id must be canonical positive-ID text.")
    number = clean_text(args.number, "number", 100)
    expect_no_reference = bool(args.expect_no_reference)
    raw_expected = args.expected_reference
    if expect_no_reference:
        if raw_expected not in (None, ""):
            raise RenderedOrderVerificationError(
                "--expect-no-reference cannot be combined with --expected-reference."
            )
        expected = ""
    else:
        expected = clean_text(raw_expected, "expected_reference", 100)
    if expected and INTERNAL_DOCUMENT_RE.fullmatch(expected):
        raise RenderedOrderVerificationError("REFUSED: an internal FRP Depot document number is not a customer PO.")
    output = clean_text(args.output, "output", 1000)
    return {
        "kind": kind, "record_id": record_id, "number": number,
        "expected_reference": expected, "expect_no_reference": expect_no_reference,
        "output": output,
    }


def organization_context() -> tuple[str, str, str]:
    token, vault = zoho_tool.refresh_access_token()
    domain = str(vault.get("api_domain") or "").rstrip("/")
    if domain != zoho_tool.EXPECTED_API_DOMAIN:
        raise RenderedOrderVerificationError("REFUSED: Zoho returned a non-Canadian API domain.")
    organization_id = str(vault.get("books_organization_id") or "")
    if not ZOHO_ID_RE.fullmatch(organization_id):
        raise RenderedOrderVerificationError("The saved FRP Depot Books organization ID is invalid.")
    return token, domain, organization_id


def record_path(selection: dict[str, Any], organization_id: str, pdf: bool = False) -> str:
    config = KINDS[selection["kind"]]
    query: dict[str, str] = {"organization_id": organization_id}
    if pdf:
        query["accept"] = "pdf"
    return f"/books/v3/{config['segment']}/{selection['record_id']}?{urlencode(query)}"


def fetch_json(token: str, domain: str, path: str) -> dict[str, Any]:
    try:
        result = zoho_tool.api_get(token, domain, path)
    except Exception as exc:  # Zoho helper redacts/normalizes transport failures
        raise RenderedOrderVerificationError(f"Live Zoho record read failed: {exc}") from exc
    if not isinstance(result, dict):
        raise RenderedOrderVerificationError("Live Zoho record read did not return an object.")
    return result


def fetch_pdf(token: str, domain: str, path: str) -> bytes:
    request = Request(
        domain + path,
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/pdf"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read(MAX_PDF_BYTES + 1)
            content_type = str(response.headers.get("Content-Type") or "")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RenderedOrderVerificationError(f"Rendered document GET failed with HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RenderedOrderVerificationError(f"Rendered document GET is indeterminate: {exc}") from exc
    if len(raw) > MAX_PDF_BYTES:
        raise RenderedOrderVerificationError(f"Rendered document exceeds {MAX_PDF_BYTES} bytes.")
    if not raw.startswith(b"%PDF-") or "pdf" not in content_type.casefold():
        raise RenderedOrderVerificationError(f"Zoho did not return a rendered PDF (content type {content_type!r}).")
    return raw


def extract_pdf_text(raw: bytes) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise RenderedOrderVerificationError("PyMuPDF is unavailable; rendered verification NOT PROVEN.") from exc
    try:
        with fitz.open(stream=raw, filetype="pdf") as document:
            return "\n".join(page.get_text() for page in document)
    except Exception as exc:
        raise RenderedOrderVerificationError(f"Rendered PDF text extraction failed: {exc}") from exc


def displayed_reference(text: str, label: str) -> tuple[bool, str]:
    flat = re.sub(r"\s+", " ", text)
    match = re.search(re.escape(label) + r"\s*:\s*(\S+)", flat)
    return (False, "") if not match else (True, match.group(1))


def verify(selection: dict[str, Any], token: str, domain: str, organization_id: str) -> dict[str, Any]:
    config = KINDS[selection["kind"]]
    api_path = record_path(selection, organization_id)
    result = fetch_json(token, domain, api_path)
    record = result.get(config["key"])
    if not isinstance(record, dict):
        raise RenderedOrderVerificationError(f"Live response lacks {config['key']}.")
    if str(record.get("customer_id") or "") == "":
        raise RenderedOrderVerificationError("Live record has no customer_id; identity NOT PROVEN.")
    if str(record.get(config["number_field"]) or "") != selection["number"]:
        raise RenderedOrderVerificationError("Live document number does not match the declared document.")
    if str(record.get("reference_number") or "") != selection["expected_reference"]:
        raise RenderedOrderVerificationError(
            f"Live API Reference# is {record.get('reference_number')!r}, not {selection['expected_reference']!r}."
        )

    pdf_path = record_path(selection, organization_id, pdf=True)
    raw = fetch_pdf(token, domain, pdf_path)
    text = extract_pdf_text(raw)
    label_present, shown = displayed_reference(text, config["label"])
    flattened = re.sub(r"\s+", " ", text)
    if selection["expect_no_reference"]:
        if config["label"] in flattened:
            raise RenderedOrderVerificationError(
                f"Rendered PDF still exposes the {config['label']} caption under the explicit no-PO exception; "
                "blank visibility is NOT PROVEN."
            )
        shown = ""
    else:
        if not label_present:
            raise RenderedOrderVerificationError(f"Rendered PDF does not expose the {config['label']} caption.")
        if shown != selection["expected_reference"]:
            raise RenderedOrderVerificationError(
                f"Rendered {config['label']} is {shown!r}, not {selection['expected_reference']!r}."
            )
    return {
        "status": "API_AND_RENDERED_REFERENCE_VERIFIED",
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "kind": selection["kind"],
        "record_id": selection["record_id"],
        "number": selection["number"],
        "customer_id": str(record.get("customer_id")),
        "api_reference_number": str(record.get("reference_number")),
        "explicit_no_po_exception": bool(selection["expect_no_reference"]),
        "rendered": {
            "endpoint": pdf_path,
            "label": config["label"],
            "displayed_reference": shown,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        "business_writes": 0,
        "emails_sent": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify API + rendered customer PO for one FRP Depot Zoho document")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--number", required=True)
    reference_group = parser.add_mutually_exclusive_group(required=True)
    reference_group.add_argument("--expected-reference")
    reference_group.add_argument("--expect-no-reference", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        selection = validate_args(args)
        token, domain, organization_id = organization_context()
        report = verify(selection, token, domain, organization_id)
        output = Path(selection["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except (RenderedOrderVerificationError, zoho_tool.ZohoError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
