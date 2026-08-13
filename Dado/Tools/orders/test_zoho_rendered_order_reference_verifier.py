from __future__ import annotations

import ast
import hashlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import fitz

import zoho_rendered_order_reference_verifier as verifier


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/pdf") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]


def make_pdf(label: str, value: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), f"FRP DEPOTS\n{label} : {value}\nCustomer document")
    raw = document.tobytes()
    document.close()
    return raw


class RenderedVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = {
            "kind": "invoice",
            "record_id": "96274000000312107",
            "number": "INV-000014",
            "expected_reference": "104662",
            "expect_no_reference": False,
            "output": "x.json",
        }

    def args(self, **changes) -> Namespace:
        values = dict(self.selection)
        values.update(changes)
        return Namespace(
            kind=values["kind"],
            record_id=values["record_id"],
            number=values["number"],
            expected_reference=values["expected_reference"],
            expect_no_reference=values["expect_no_reference"],
            output=values["output"],
        )

    def live_result(self, kind: str = "invoice", reference: str = "104662") -> dict:
        config = verifier.KINDS[kind]
        return {config["key"]: {
            config["number_field"]: self.selection["number"],
            "customer_id": "96274000000060019",
            "reference_number": reference,
        }}

    def verify_with(self, kind: str, label: str, number: str, reference: str = "104662") -> dict:
        selection = dict(self.selection, kind=kind, number=number)
        config = verifier.KINDS[kind]
        live = {config["key"]: {
            config["number_field"]: number,
            "customer_id": "96274000000060019",
            "reference_number": reference,
        }}
        with mock.patch.object(verifier, "fetch_json", return_value=live), mock.patch.object(
            verifier, "fetch_pdf", return_value=make_pdf(label, reference)
        ):
            return verifier.verify(selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_invoice_api_and_rendered_po_caption(self) -> None:
        report = self.verify_with("invoice", "P.O.#", "INV-000014")
        self.assertEqual(report["status"], "API_AND_RENDERED_REFERENCE_VERIFIED")
        self.assertEqual(report["rendered"]["displayed_reference"], "104662")
        self.assertEqual(report["business_writes"], 0)
        self.assertEqual(report["emails_sent"], 0)

    def test_quote_reference_caption(self) -> None:
        report = self.verify_with("quote", "Reference#", "QT-000099")
        self.assertEqual(report["rendered"]["label"], "Reference#")

    def test_sales_order_ref_caption(self) -> None:
        report = self.verify_with("sales_order", "Ref#", "SO-00099")
        self.assertEqual(report["rendered"]["label"], "Ref#")

    def test_wrong_api_reference_refused_before_pdf(self) -> None:
        with mock.patch.object(verifier, "fetch_json", return_value=self.live_result(reference="SO-00013")), mock.patch.object(
            verifier, "fetch_pdf"
        ) as pdf:
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "Live API Reference"):
                verifier.verify(self.selection, "token", "https://www.zohoapis.ca", "110002157575")
        pdf.assert_not_called()

    def test_wrong_live_number_refused(self) -> None:
        live = self.live_result()
        live["invoice"]["invoice_number"] = "INV-999999"
        with mock.patch.object(verifier, "fetch_json", return_value=live):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "number does not match"):
                verifier.verify(self.selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_missing_customer_identity_refused(self) -> None:
        live = self.live_result()
        live["invoice"]["customer_id"] = ""
        with mock.patch.object(verifier, "fetch_json", return_value=live):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "customer_id"):
                verifier.verify(self.selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_wrong_pdf_reference_refused(self) -> None:
        with mock.patch.object(verifier, "fetch_json", return_value=self.live_result()), mock.patch.object(
            verifier, "fetch_pdf", return_value=make_pdf("P.O.#", "SO-00013")
        ):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "Rendered P.O.#"):
                verifier.verify(self.selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_missing_pdf_caption_refused(self) -> None:
        with mock.patch.object(verifier, "fetch_json", return_value=self.live_result()), mock.patch.object(
            verifier, "fetch_pdf", return_value=make_pdf("Other", "104662")
        ):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "does not expose"):
                verifier.verify(self.selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_pdf_fetch_is_exact_get_route(self) -> None:
        body = make_pdf("P.O.#", "104662")
        captured = []

        def transport(request, timeout=90):
            captured.append((request.get_method(), request.full_url, request.headers))
            return FakeResponse(body)

        with mock.patch.object(verifier, "urlopen", side_effect=transport):
            result = verifier.fetch_pdf(
                "secret-token",
                "https://www.zohoapis.ca",
                "/books/v3/invoices/96274000000312107?organization_id=110002157575&accept=pdf",
            )
        self.assertEqual(result, body)
        self.assertEqual(captured[0][0], "GET")
        self.assertIn("/books/v3/invoices/96274000000312107?", captured[0][1])
        self.assertIn("accept=pdf", captured[0][1])

    def test_non_pdf_refused(self) -> None:
        with mock.patch.object(verifier, "urlopen", return_value=FakeResponse(b'{"code":0}', "application/json")):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "did not return"):
                verifier.fetch_pdf("token", "https://www.zohoapis.ca", "/x")

    def test_oversize_pdf_refused(self) -> None:
        body = b"%PDF-" + b"x" * (verifier.MAX_PDF_BYTES + 10)
        with mock.patch.object(verifier, "urlopen", return_value=FakeResponse(body)):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "exceeds"):
                verifier.fetch_pdf("token", "https://www.zohoapis.ca", "/x")

    def test_internal_document_number_refused_as_po(self) -> None:
        with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "internal FRP Depot"):
            verifier.validate_args(self.args(expected_reference="SO-00013"))

    def test_no_reference_mode_is_explicit_and_exclusive(self) -> None:
        args = self.args(expected_reference=None, expect_no_reference=True)
        selection = verifier.validate_args(args)
        self.assertTrue(selection["expect_no_reference"])
        self.assertEqual(selection["expected_reference"], "")

    def test_no_reference_mode_refuses_combined_value(self) -> None:
        with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "cannot be combined"):
            verifier.validate_args(self.args(expect_no_reference=True))

    def test_explicit_no_reference_verifies_blank_api_and_omitted_caption(self) -> None:
        selection = dict(self.selection, expected_reference="", expect_no_reference=True)
        live = self.live_result(reference="")
        with mock.patch.object(verifier, "fetch_json", return_value=live), mock.patch.object(
            verifier, "fetch_pdf", return_value=make_pdf("Customer", "document")
        ):
            report = verifier.verify(
                selection, "token", "https://www.zohoapis.ca", "110002157575"
            )
        self.assertTrue(report["explicit_no_po_exception"])
        self.assertEqual(report["api_reference_number"], "")
        self.assertEqual(report["rendered"]["displayed_reference"], "")

    def test_explicit_no_reference_refuses_rendered_caption(self) -> None:
        selection = dict(self.selection, expected_reference="", expect_no_reference=True)
        live = self.live_result(reference="")
        with mock.patch.object(verifier, "fetch_json", return_value=live), mock.patch.object(
            verifier, "fetch_pdf", return_value=make_pdf("P.O.#", "SO-00013")
        ):
            with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "blank visibility is NOT PROVEN"):
                verifier.verify(selection, "token", "https://www.zohoapis.ca", "110002157575")

    def test_noncanonical_id_refused(self) -> None:
        with self.assertRaisesRegex(verifier.RenderedOrderVerificationError, "canonical"):
            verifier.validate_args(self.args(record_id="096274000000312107"))

    def test_record_path_is_bounded_to_selected_type_and_id(self) -> None:
        path = verifier.record_path(self.selection, "110002157575", pdf=True)
        self.assertEqual(
            path,
            "/books/v3/invoices/96274000000312107?organization_id=110002157575&accept=pdf",
        )

    def test_main_writes_only_local_evidence_file(self) -> None:
        report = {
            "status": "API_AND_RENDERED_REFERENCE_VERIFIED",
            "business_writes": 0,
            "emails_sent": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            with mock.patch.object(verifier, "organization_context", return_value=("token", "https://www.zohoapis.ca", "110002157575")), mock.patch.object(
                verifier, "verify", return_value=report
            ), mock.patch("sys.stdout", new=io.StringIO()):
                exit_code = verifier.main([
                    "--kind", "invoice", "--record-id", "96274000000312107",
                    "--number", "INV-000014", "--expected-reference", "104662",
                    "--output", str(output),
                ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

    def test_source_has_no_business_write_or_mail_calls(self) -> None:
        tree = ast.parse(Path(verifier.__file__).read_text(encoding="utf-8"))
        method_literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        self.assertFalse({"POST", "PUT", "PATCH", "DELETE"} & method_literals)
        self.assertFalse({"api_post", "api_put", "send", "replyAll", "draft"} & calls)

    def test_pdf_hash_evidence_matches_exact_received_bytes(self) -> None:
        raw = make_pdf("P.O.#", "104662")
        with mock.patch.object(verifier, "fetch_json", return_value=self.live_result()), mock.patch.object(
            verifier, "fetch_pdf", return_value=raw
        ):
            report = verifier.verify(
                self.selection, "token", "https://www.zohoapis.ca", "110002157575"
            )
        self.assertEqual(report["rendered"]["sha256"], hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
