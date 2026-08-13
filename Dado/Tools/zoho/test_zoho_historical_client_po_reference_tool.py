"""No-network/adversarial tests for the fixed historical client-PO repair tool.

Every test mocks the network, redirects plan/lock directories into a temporary
folder, and never touches the live vault, a browser or a real Zoho record.
"""
from __future__ import annotations

import argparse
import ast
import copy
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

import zoho_historical_client_po_reference_tool as tool
import zoho_tool

HERE = Path(__file__).resolve().parent
ORG_ID = "110002157575"
ORG_NAME = "FRP DEPOTS"

# Records that exist in the audit/recovery sources but are permanently outside
# this tool. INV-000051 / INV-000053 / SO-00050 belong to other commissions.
FOREIGN_IDS = ("96274000001559012", "96274000001605003", "96274000001556001")
FOREIGN_KEYS = ("inv000051", "inv000053", "so00050", "SO-00050", "96274000001559012")


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str = "application/json"):
        self.payload = payload
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amount: int | None = None) -> bytes:
        return self.payload if amount is None else self.payload[:amount]


def fake_line(record: dict, index: int) -> dict:
    """A realistic line carrying identity, money, tax and fulfilment links."""
    line = {
        "line_item_id": f"{int(record['record_id']) + 900 + index}",
        "item_id": "96274000000019627",
        "name": 'FRP STUB FLANGE-2"/150PSI/D411',
        "description": "FRP STUB FLANGE",
        "quantity": 6.0,
        "rate": 59.4,
        "discount": 0.0,
        "tax_id": "96274000000035512",
        "tax_name": "GST",
        "tax_percentage": 5.0,
        "unit": "pcs",
        "item_order": index + 1,
        "item_total": 356.4,
        "sku": "SF2150411",
    }
    if record["kind"] == tool.INVOICE_KIND:
        line["salesorder_item_id"] = "96274000000317005"
    return line


def fake_record(record: dict, reference: str | None = None) -> dict:
    """A live Zoho body for one fixed record, including its mirror array."""
    kind_key = tool.RECORD_KEYS[record["kind"]]
    case = tool.CASES_BY_KEY[record["case_key"]]
    reference = record.get("required_reference", record.get("before", "")) if reference is None else reference
    body = {
        f"{kind_key}_id": record["record_id"],
        f"{kind_key}_number": record["number"],
        "customer_id": case["customer_id"],
        "customer_name": case["customer_name"],
        "status": record["status"],
        "currency_code": record["currency_code"],
        "reference_number": reference,
        "date": "2026-01-23",
        "exchange_rate": 1.0,
        "total": 374.22,
        "balance": 0.0,
        "sub_total": 356.4,
        "tax_total": 17.82,
        "adjustment": 0.0,
        "shipping_charge": 0.0,
        "discount": 0.0,
        "notes": "Looking forward for your business.",
        "terms": "Net 15",
        "custom_fields": [],
        "documents": [],
        "billing_address": {"address": "3855 rang St-Alexis", "zip": "G0X 3K0"},
        "shipping_address": {"address": "3855 rang St-Alexis", "zip": "G0X 3K0"},
        "contact_persons": ["96274000000060021"],
        "template_id": "96274000000000537",
        "template_name": "Standard Template",
        "salesperson_id": "",
        "line_items": [fake_line(record, 0)],
        "last_modified_time": "2026-03-05T19:14:09-0500",
        "last_modified_by_id": "96274000000014001",
    }
    if record["kind"] == tool.SALESORDER_KIND:
        body.update({
            "order_status": record["order_status"],
            "invoiced_status": record["invoiced_status"],
            "shipped_status": record["shipped_status"],
            "paid_status": record["paid_status"],
            "estimate_id": "96274000000312001",
            "total_quantity": 6.0,
            "invoices": [
                {
                    "invoice_id": tool.ALL_BY_KEY[key]["record_id"],
                    "invoice_number": tool.ALL_BY_KEY[key]["number"],
                    "reference_number": tool.ALL_BY_KEY[key].get(
                        "required_reference", tool.ALL_BY_KEY[key].get("before", "")
                    ),
                    "status": "paid",
                    "total": 374.22,
                    "balance": 0.0,
                }
                for key in record["linked"]
            ],
        })
    else:
        order = tool.ALL_BY_KEY[record["linked"][0]]
        body.update({
            "salesorder_id": record["salesorder_id"],
            "salesorder_number": record["salesorder_number"],
            "payment_made": 374.22,
            "credits_applied": 0.0,
            "write_off_amount": 0.0,
            "due_date": "2026-02-07",
            "salesorders": [
                {
                    "salesorder_id": order["record_id"],
                    "salesorder_number": order["number"],
                    "reference_number": order.get("before", ""),
                    "salesorder_order_status": "closed",
                    "total": 374.22,
                }
            ],
        })
    return body


def rendered_text(record: dict, value: str) -> str:
    label = tool.PDF_LABELS[record["kind"]]
    if not value:
        return "FRP DEPOTS\nSALES ORDER\nSub Total\n356.40\nTotal\nCAD374.22\n"
    return f"Sub Total\n356.40\n{label} :\n{value}\nFRP DEPOTS\n{record['number']}\n"


class RepairCase(unittest.TestCase):
    record_key = "so00013-order"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "plans"
        self.lock_dir = self.plan_dir / ".commit-locks"
        self.bodies = {row["record_id"]: fake_record(row) for row in tool.ALL_BY_KEY.values()}
        self.write_calls: list[dict] = []
        self.pdf_calls: list[str] = []
        self.pdf_fail: Exception | None = None
        self.extract_fail: Exception | None = None
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": ORG_ID,
            "scopes": [
                tool.SALESORDER_SCOPE, tool.INVOICE_SCOPE,
                tool.SALESORDER_READ_SCOPE, tool.INVOICE_READ_SCOPE,
            ],
        }
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "LOCK_DIR", self.lock_dir),
            mock.patch.object(tool.zoho_tool, "load_vault", side_effect=lambda: self.vault),
            mock.patch.object(
                tool.zoho_tool, "refresh_access_token", side_effect=lambda vault=None: ("token", self.vault)
            ),
            mock.patch.object(tool.zoho_tool, "save_vault"),
            mock.patch.object(tool.zoho_tool, "append_receipt"),
            mock.patch.object(tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(tool, "urlopen", side_effect=self.transport),
            mock.patch.object(tool, "extract_pdf_text", side_effect=self.extract),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_mock = started[2]
        self.refresh_mock = started[3]
        self.api_mock = started[6]
        self.urlopen_mock = started[7]
        self.addCleanup(self.stop_all)

    def stop_all(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    # -- fakes ------------------------------------------------------------

    def api_get(self, token: str, domain: str, path: str) -> dict:
        if path == "/books/v3/organizations":
            return {"organizations": [{"organization_id": ORG_ID, "name": ORG_NAME}]}
        for kind, segment in tool.SEGMENTS.items():
            prefix = f"/books/v3/{segment}/"
            if path.startswith(prefix):
                record_id = path[len(prefix):].split("?", 1)[0]
                if record_id not in self.bodies:
                    raise AssertionError(f"read of an unexpected record {record_id}")
                return {tool.RECORD_KEYS[kind]: copy.deepcopy(self.bodies[record_id])}
        raise AssertionError(f"unexpected GET {path}")

    def transport(self, request, timeout=90):
        method = request.get_method()
        if method == "GET":
            self.pdf_calls.append(request.full_url)
            if self.pdf_fail:
                raise self.pdf_fail
            return FakeResponse(b"%PDF-1.4 fake", "application/pdf;charset=UTF-8")
        if method != "PUT":
            raise AssertionError(f"forbidden method {method}")
        payload = json.loads(request.data.decode("utf-8"))
        self.write_calls.append({
            "method": method, "url": request.full_url, "payload": payload,
            "lock_exists": bool(list(self.lock_dir.glob("*.json"))),
        })
        if getattr(self, "write_error", None):
            raise self.write_error
        record_id = request.full_url.split("?", 1)[0].rsplit("/", 1)[-1]
        body = self.bodies[record_id]
        body["reference_number"] = payload["reference_number"]
        body["last_modified_time"] = "2026-08-12T18:00:00-0400"
        mutate = getattr(self, "mutate_after", None)
        if mutate:
            mutate(body)
        return FakeResponse(json.dumps({"code": 0, "message": "updated"}).encode("utf-8"))

    def extract(self, raw: bytes) -> str:
        if self.extract_fail:
            raise self.extract_fail
        override = getattr(self, "rendered_text_override", None)
        if override is not None:
            return override
        # Derive the document from the URL the tool actually fetched, so a test
        # that stages a different record cannot silently render the wrong one.
        record_id = self.pdf_calls[-1].split("?", 1)[0].rsplit("/", 1)[-1]
        record = next(r for r in tool.ALL_BY_KEY.values() if r["record_id"] == record_id)
        shown = getattr(self, "rendered_value", None)
        if shown is None:
            shown = self.bodies[record_id]["reference_number"]
        return rendered_text(record, shown)

    # -- helpers ----------------------------------------------------------

    def stage(self, record_key: str | None = None) -> Path:
        key = record_key or self.record_key
        before = set(self.plan_dir.glob("*.json")) if self.plan_dir.exists() else set()
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_stage(argparse.Namespace(record_key=key))
        # Plan filenames carry a whole-second timestamp, so two plans staged in
        # the same second sort by hash. Identify the new one by difference.
        fresh = sorted(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(fresh), 1, "staging must produce exactly one new plan")
        self.stage_output = output.getvalue()
        return fresh[0]

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))
        return output.getvalue()

    def rewrite(self, path: Path, mutate) -> Path:
        """Edit a staged plan and re-sign it, so semantics are tested, not the hash."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("sha256")
        mutate(raw)
        raw["sha256"] = tool.digest_for(raw)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# 1, 5 -- the fixed set and the exact evidenced spellings
# ---------------------------------------------------------------------------


class TestFixedScope(unittest.TestCase):
    def test_exactly_twelve_writable_records_with_exact_values(self):
        expected = {
            "so00013-order": ("salesorder", "96274000000317001", "SO-00013", "QT-000012", "104662"),
            "inv000014-invoice": ("invoice", "96274000000312107", "INV-000014", "SO-00013", "104662"),
            "so00016-order": ("salesorder", "96274000000409073", "SO-00016", "QT-000015", "PO5072"),
            "inv000018-invoice": ("invoice", "96274000000411047", "INV-000018", "SO-00016", "PO5072"),
            "so00019-order": ("salesorder", "96274000000466136", "SO-00019", "QT-000016", "PO5079"),
            "so00021-order": ("salesorder", "96274000000575001", "SO-00021", "QT-000017", "PO26078"),
            "inv000023-invoice": ("invoice", "96274000000579007", "INV-000023", "SO-00021", "PO26078"),
            "so00040-order": ("salesorder", "96274000001030001", "SO-00040", "QT-000022", "2127"),
            "inv000039-invoice": ("invoice", "96274000001052009", "INV-000039", "SO-00040", "2127"),
            "so00044-order": ("salesorder", "96274000001140080", "SO-00044", "", "4500021643"),
            "inv000043-invoice": ("invoice", "96274000001140095", "INV-000043", "SO-00044", "4500021643"),
            "inv000045-invoice": ("invoice", "96274000001212003", "INV-000045", "SO-00044", "4500021643"),
        }
        self.assertEqual(len(tool.WRITABLE_RECORDS), 12)
        self.assertEqual(set(tool.WRITABLE_BY_KEY), set(expected))
        for key, (kind, record_id, number, before, target) in expected.items():
            record = tool.WRITABLE_BY_KEY[key]
            self.assertEqual(
                (record["kind"], record["record_id"], record["number"], record["before"],
                 tool.target_of(record)),
                (kind, record_id, number, before, target),
            )

    def test_prefixed_spellings_are_never_the_normalized_digits(self):
        # The recovery artifact normalized these to 5079 / 26078. The externally
        # evidenced spelling wins.
        self.assertEqual(tool.target_of(tool.WRITABLE_BY_KEY["so00019-order"]), "PO5079")
        self.assertEqual(tool.target_of(tool.WRITABLE_BY_KEY["so00021-order"]), "PO26078")
        self.assertEqual(tool.target_of(tool.WRITABLE_BY_KEY["inv000023-invoice"]), "PO26078")
        for bad in ("5079", "26078", "PO2127", "PO104662", "po5079"):
            self.assertNotIn(bad, {tool.target_of(r) for r in tool.WRITABLE_RECORDS})

    def test_six_cases_and_six_orders_six_invoices(self):
        self.assertEqual(len(tool.CASES), 6)
        kinds = [record["kind"] for record in tool.WRITABLE_RECORDS]
        self.assertEqual(kinds.count(tool.SALESORDER_KIND), 6)
        self.assertEqual(kinds.count(tool.INVOICE_KIND), 6)


# ---------------------------------------------------------------------------
# 2, 3, 4, 21 -- everything outside the twelve is unreachable
# ---------------------------------------------------------------------------


class TestUnreachable(unittest.TestCase):
    def test_inv000020_is_verification_only(self):
        self.assertEqual(len(tool.VERIFY_ONLY_RECORDS), 1)
        verify_only = tool.VERIFY_ONLY_RECORDS[0]
        self.assertEqual(verify_only["record_id"], "96274000000552009")
        self.assertEqual(verify_only["required_reference"], "PO5079")
        self.assertNotIn(verify_only["record_id"], tool.WRITABLE_IDS)
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "verification-only"):
            tool.select_record(verify_only["record_key"])

    def test_every_non_fixed_source_id_is_refused(self):
        audit = json.loads(tool.SOURCE_FILES["audit"][0].read_text(encoding="utf-8"))
        recovery = json.loads(tool.SOURCE_FILES["recovery"][0].read_text(encoding="utf-8"))
        ids: set[str] = set()
        for row in audit["salesorders"]:
            ids.add(str(row["salesorder_id"]))
            ids.update(str(value) for value in row["invoice_ids"])
            ids.update(str(entry["invoice_id"]) for entry in row["invoice_candidate_details"])
            if row.get("quote_id"):
                ids.add(str(row["quote_id"]))
        for row in recovery["salesorders"]:
            for field in ("salesorder_id", "quote_id"):
                if row.get(field):
                    ids.add(str(row[field]))
        outside = sorted(ids - tool.READABLE_IDS)
        self.assertGreater(len(outside), 40, "the sources must contain many non-fixed records")
        for record_id in outside:
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.select_record(record_id)
            self.assertNotIn(record_id, tool.WRITABLE_IDS)
            self.assertNotIn(f"/books/v3/salesorders/{record_id}", tool.WRITABLE_PATHS)
            self.assertNotIn(f"/books/v3/invoices/{record_id}", tool.WRITABLE_PATHS)

    def test_ambiguous_and_no_evidence_orders_are_unreachable(self):
        recovery = json.loads(tool.SOURCE_FILES["recovery"][0].read_text(encoding="utf-8"))
        excluded = [
            row for row in recovery["salesorders"]
            if str(row.get("recovery_state") or row.get("state") or "") not in ("certain", "")
        ]
        checked = 0
        for row in recovery["salesorders"]:
            record_id = str(row.get("salesorder_id") or "")
            if not record_id or record_id in tool.WRITABLE_IDS:
                continue
            checked += 1
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.select_record(record_id)
        self.assertGreaterEqual(checked + len(excluded), 15)

    def test_other_commissions_records_are_unreachable(self):
        for record_id in FOREIGN_IDS:
            self.assertNotIn(record_id, tool.READABLE_IDS)
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.select_record(record_id)
        for key in FOREIGN_KEYS:
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.select_record(key)

    def test_selector_must_be_unpadded_known_text(self):
        for bad in (" so00013-order", "so00013-order ", "", "SO00013-ORDER", None, 12, ["so00013-order"]):
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.select_record(bad)


# ---------------------------------------------------------------------------
# 6 -- artifact hashes fail closed
# ---------------------------------------------------------------------------


class TestSourceIntegrity(unittest.TestCase):
    def test_real_artifacts_match_the_pinned_digests(self):
        files = tool.verify_source_files()
        self.assertEqual(files["scope_artifact"]["sha256"], tool.SCOPE_ARTIFACT_SHA256)
        for label, (_, wanted) in tool.SOURCE_FILES.items():
            self.assertEqual(files["sources"][label]["sha256"], wanted)
        self.assertEqual(files["api_contract"]["salesorder_put_required"], ["customer_id"])
        self.assertEqual(files["api_contract"]["invoice_put_required"], ["customer_id", "line_items"])

    def test_scope_artifact_hash_mismatch_fails_closed(self):
        with mock.patch.object(tool, "SCOPE_ARTIFACT_SHA256", "0" * 64):
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "scope artifact SHA-256"):
                tool.verify_source_files()

    def test_each_source_hash_mismatch_fails_closed(self):
        for label in tool.SOURCE_FILES:
            broken = dict(tool.SOURCE_FILES)
            broken[label] = (tool.SOURCE_FILES[label][0], "1" * 64)
            with mock.patch.object(tool, "SOURCE_FILES", broken):
                with self.assertRaisesRegex(tool.ClientPoReferenceError, "source SHA-256 mismatch"):
                    tool.verify_source_files()

    def test_api_contract_hash_mismatch_fails_closed(self):
        for name in tool.CONTRACT_FILES:
            broken = dict(tool.CONTRACT_FILES)
            broken[name] = "2" * 64
            with mock.patch.object(tool, "CONTRACT_FILES", broken):
                with self.assertRaisesRegex(tool.ClientPoReferenceError, "API contract SHA-256"):
                    tool.verify_source_files()

    def test_missing_artifact_fails_closed(self):
        with mock.patch.object(tool, "SCOPE_ARTIFACT", tool.WORKING / "does_not_exist.json"):
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "unreadable"):
                tool.verify_source_files()

    def test_artifact_cannot_widen_the_fixed_set(self):
        scope = json.loads(tool.SCOPE_ARTIFACT.read_text(encoding="utf-8"))
        scope["fixed_cases"][0]["salesorder"]["id"] = "96274000009999999"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "cannot reach"):
            tool.verify_scope_agrees(scope)

    def test_artifact_cannot_change_a_target_or_before(self):
        scope = json.loads(tool.SCOPE_ARTIFACT.read_text(encoding="utf-8"))
        scope["fixed_cases"][0]["client_po_reference"] = "999999"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "target PO changed"):
            tool.verify_scope_agrees(scope)
        scope = json.loads(tool.SCOPE_ARTIFACT.read_text(encoding="utf-8"))
        scope["fixed_cases"][0]["salesorder"]["current_reference"] = "QT-999999"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "before reference changed"):
            tool.verify_scope_agrees(scope)

    def test_artifact_cannot_promote_the_verification_only_invoice(self):
        scope = json.loads(tool.SCOPE_ARTIFACT.read_text(encoding="utf-8"))
        for case in scope["fixed_cases"]:
            for invoice in case.get("invoices") or []:
                if invoice["id"] == "96274000000552009":
                    invoice["action"] = "change_reference"
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.verify_scope_agrees(scope)


# ---------------------------------------------------------------------------
# 7, 8 -- staging refuses drift and never stages an already-correct record
# ---------------------------------------------------------------------------


class TestStaging(RepairCase):
    def setUp(self) -> None:
        super().setUp()
        self.source_patch = mock.patch.object(
            tool, "verify_source_files",
            return_value={
                "scope_artifact": {"path": "x", "sha256": tool.SCOPE_ARTIFACT_SHA256, "bytes": 1},
                "sources": {
                    label: {"path": "x", "sha256": wanted, "bytes": 1}
                    for label, (_, wanted) in tool.SOURCE_FILES.items()
                },
                "api_contract": {"files": {}, "salesorder_put_required": ["customer_id"],
                                 "invoice_put_required": ["customer_id", "line_items"],
                                 "rendered_document_read": "GET the same record with accept=pdf",
                                 "source": "pinned"},
            },
        )
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def test_stage_makes_zero_writes_and_reads_a_stable_rehearsal(self):
        path = self.stage()
        plan = tool.load_plan(str(path))
        self.assertEqual(self.write_calls, [])
        self.assertEqual(plan["live_evidence"]["stable_read_count"], tool.STABLE_READS)
        self.assertEqual(plan["selection"]["target_reference"], "104662")
        self.assertEqual(plan["endpoint"], {"method": "PUT", "path": "/books/v3/salesorders/96274000000317001"})
        self.assertIn("STAGED ONLY - ZERO ZOHO WRITES", self.stage_output)
        # organizations + 3 rounds x (record + 1 dependency) + 1 payload read
        self.assertEqual(self.api_mock.call_count, 1 + 3 * 2 + 1)
        self.assertEqual(len(self.pdf_calls), 1)
        self.assertIn("accept=pdf", self.pdf_calls[0])

    def test_stage_refuses_when_already_correct(self):
        self.bodies["96274000000317001"]["reference_number"] = "104662"
        self.bodies["96274000000312107"]["salesorders"][0]["reference_number"] = "104662"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "ALREADY CORRECT"):
            self.stage()
        self.assertFalse(list(self.plan_dir.glob("*.json")) if self.plan_dir.exists() else [])

    def test_stage_refuses_a_third_reference_value(self):
        self.bodies["96274000000317001"]["reference_number"] = "QT-999999"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "neither the fixed before"):
            self.stage()

    def test_stage_refuses_identity_drift(self):
        for field, value in (
            ("salesorder_number", "SO-99999"),
            ("customer_id", "96274000009999999"),
            ("customer_name", "Someone Else"),
            ("status", "draft"),
            ("order_status", "open"),
            ("invoiced_status", "not_invoiced"),
            ("shipped_status", "pending"),
            ("currency_code", "USD"),
        ):
            with self.subTest(field=field):
                original = self.bodies["96274000000317001"][field]
                self.bodies["96274000000317001"][field] = value
                with self.assertRaises(tool.ClientPoReferenceError):
                    self.stage()
                self.bodies["96274000000317001"][field] = original

    def test_stage_refuses_broken_linkage(self):
        self.bodies["96274000000317001"]["invoices"] = []
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "linked invoice set changed"):
            self.stage()

    def test_stage_refuses_unknown_mirrored_reference(self):
        self.bodies["96274000000317001"]["invoices"][0]["reference_number"] = "SOMETHING-ELSE"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "neither its fixed before"):
            self.stage()

    def test_stage_refuses_when_the_verification_only_invoice_moved(self):
        self.bodies["96274000000552009"]["reference_number"] = "PO9999"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "must still read"):
            self.stage("so00019-order")

    def test_stage_refuses_when_live_state_moves_between_reads(self):
        original = tool.live_round
        calls = {"n": 0}

        def moving(*args):
            result = original(*args)
            calls["n"] += 1
            if calls["n"] == 2:
                result["record"]["total"] = "999.99"
            return result

        with mock.patch.object(tool, "live_round", side_effect=moving):
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "moved during"):
                self.stage()

    def test_regenerated_invoice_url_does_not_fake_business_state_drift(self):
        """Zoho regenerates invoice_url on each GET; business state is unchanged."""
        record = tool.WRITABLE_BY_KEY["inv000014-invoice"]
        first = fake_record(record)
        second = copy.deepcopy(first)
        first["invoice_url"] = "https://books.zohocloud.ca/invoice/first-token"
        second["invoice_url"] = "https://books.zohocloud.ca/invoice/second-token"
        self.assertNotEqual(first["invoice_url"], second["invoice_url"])
        self.assertEqual(
            tool.protected_fingerprint(first, record),
            tool.protected_fingerprint(second, record),
        )
        first_round = {
            "record": {
                "record_key": record["record_key"],
                "protected": tool.protected_fingerprint(first, record),
                "volatile_observed": {"invoice_url": first["invoice_url"]},
            },
            "dependencies": [],
        }
        second_round = {
            "record": {
                "record_key": record["record_key"],
                "protected": tool.protected_fingerprint(second, record),
                "volatile_observed": {"invoice_url": second["invoice_url"]},
            },
            "dependencies": [],
        }
        self.assertNotEqual(tool.digest_for(first_round), tool.digest_for(second_round))
        self.assertEqual(
            tool.stable_business_state(first_round),
            tool.stable_business_state(second_round),
        )
        self.assertEqual(
            tool.digest_for(tool.stable_business_state(first_round)),
            tool.digest_for(tool.stable_business_state(second_round)),
        )

    def test_stage_refuses_without_the_update_scope(self):
        self.vault["scopes"] = [tool.SALESORDER_READ_SCOPE, tool.INVOICE_READ_SCOPE]
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "REFUSED BEFORE STAGING"):
            self.stage()
        self.assertEqual(self.api_mock.call_count, 0)
        self.assertEqual(self.write_calls, [])

    def test_stage_refuses_a_non_canadian_domain(self):
        self.vault["api_domain"] = "https://www.zohoapis.com"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "Canadian API domain"):
            self.stage()

    def test_stage_refuses_a_foreign_organization(self):
        self.api_mock.side_effect = lambda t, d, p: (
            {"organizations": [{"organization_id": ORG_ID, "name": "Some Other Co"}]}
            if p == "/books/v3/organizations" else self.api_get(t, d, p)
        )
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "not FRP Depot"):
            self.stage()

    def test_blank_reference_order_expects_no_caption(self):
        plan = tool.load_plan(str(self.stage("so00044-order")))
        self.assertEqual(plan["selection"]["before_reference"], "")
        self.assertFalse(plan["rendered_before"]["label_present"])
        self.assertEqual(plan["rendered_before"]["displayed_reference"], "")

    def test_blank_reference_order_refuses_if_a_caption_appears(self):
        self.rendered_value = "SOMETHING"
        self.record_key = "so00044-order"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "NOT PROVEN"):
            self.stage("so00044-order")


# ---------------------------------------------------------------------------
# 15, 17 -- payload surface
# ---------------------------------------------------------------------------


class TestPayload(RepairCase):
    def test_salesorder_payload_is_customer_and_reference_only(self):
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        payload = tool.build_payload(self.bodies[record["record_id"]], record)
        self.assertEqual(set(payload), {"customer_id", "reference_number"})
        self.assertEqual(payload["reference_number"], "104662")
        self.assertEqual(payload["customer_id"], "96274000000060019")
        self.assertNotIn("line_items", payload)

    def test_invoice_payload_resends_every_line_once_in_order_with_identity(self):
        record = tool.WRITABLE_BY_KEY["inv000039-invoice"]
        body = self.bodies[record["record_id"]]
        body["line_items"] = [fake_line(record, index) for index in range(11)]
        payload = tool.build_payload(body, record)
        self.assertEqual(set(payload), {"customer_id", "reference_number", "line_items"})
        self.assertEqual(len(payload["line_items"]), 11)
        self.assertEqual(
            [line["line_item_id"] for line in payload["line_items"]],
            [line["line_item_id"] for line in body["line_items"]],
        )
        for line in payload["line_items"]:
            self.assertTrue(set(line) <= set(tool.LINE_PUT_KEYS))
            self.assertTrue(line["line_item_id"] and line["item_id"])

    def test_payload_never_carries_a_business_field_it_must_not_change(self):
        for key in ("so00013-order", "inv000014-invoice"):
            record = tool.WRITABLE_BY_KEY[key]
            payload = tool.build_payload(self.bodies[record["record_id"]], record)
            text = json.dumps(payload).casefold()
            for forbidden in (
                "status", "invoice_number", "salesorder_number", "date", "currency",
                "exchange_rate", "total", "balance", "payment_made", "credits_applied",
                "write_off", "adjustment", "shipping_charge", "billing_address",
                "custom_field", "template_id", "notes", "terms", "email",
            ):
                self.assertNotIn(forbidden, text, f"{key} payload leaked {forbidden}")

    def test_validate_payload_rejects_widening(self):
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        for widened in (
            {"customer_id": "96274000000060019", "reference_number": "104662", "status": "draft"},
            {"customer_id": "96274000000060019", "reference_number": "104662", "line_items": []},
            {"customer_id": "96274000000060019"},
            {"reference_number": "104662"},
        ):
            with self.assertRaises(tool.ClientPoReferenceError):
                tool.validate_payload(widened, record, [])

    def test_validate_payload_rejects_wrong_target_or_customer(self):
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "target is not"):
            tool.validate_payload(
                {"customer_id": "96274000000060019", "reference_number": "104663"}, record, []
            )
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "customer is not"):
            tool.validate_payload(
                {"customer_id": "96274000000060011", "reference_number": "104662"}, record, []
            )

    def test_invoice_payload_rejects_dropped_reordered_or_extra_lines(self):
        record = tool.WRITABLE_BY_KEY["inv000014-invoice"]
        body = self.bodies[record["record_id"]]
        body["line_items"] = [fake_line(record, index) for index in range(3)]
        live_ids = [line["line_item_id"] for line in body["line_items"]]
        good = tool.build_payload(body, record)
        tool.validate_payload(copy.deepcopy(good), record, live_ids)
        dropped = copy.deepcopy(good)
        dropped["line_items"].pop()
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.validate_payload(dropped, record, live_ids)
        reordered = copy.deepcopy(good)
        reordered["line_items"].reverse()
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.validate_payload(reordered, record, live_ids)
        extra = copy.deepcopy(good)
        extra["line_items"][0]["item_total"] = 1.0
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "unsupported keys"):
            tool.validate_payload(extra, record, live_ids)


# ---------------------------------------------------------------------------
# 9, 10, 11, 12, 13, 14, 18, 19, 20 -- commit safety
# ---------------------------------------------------------------------------


class TestCommit(TestStaging):
    def test_one_record_per_plan_and_no_batch_command(self):
        parser = tool.build_parser()
        subs = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"]
        self.assertEqual(set(subs[0].choices), {"list-fixed", "stage", "commit"})
        stage_options = {
            option for action in subs[0].choices["stage"]._actions for option in action.option_strings
        }
        self.assertEqual(stage_options, {"-h", "--help", "--record-key"})
        commit_options = {
            option for action in subs[0].choices["commit"]._actions for option in action.option_strings
        }
        self.assertEqual(commit_options, {"-h", "--help", "--plan", "--approval"})
        plan = tool.load_plan(str(self.stage()))
        self.assertEqual(plan["selection"]["record_id"], "96274000000317001")
        self.assertIsInstance(plan["selection"]["record_id"], str)

    def test_approval_must_be_byte_exact_before_any_vault_or_network_use(self):
        plan = self.stage()
        self.load_mock.reset_mock()
        for bad in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "Approved", "YES", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(tool.ClientPoReferenceError, "exactly unpadded"):
                    self.commit(plan, bad)
        self.load_mock.assert_not_called()
        self.assertEqual(self.write_calls, [])
        self.assertFalse(self.lock_dir.exists())

    def test_expired_plan_refuses(self):
        path = self.stage()
        self.rewrite(path, lambda raw: raw.update({
            "created_utc": "2026-08-01T00:00:00+00:00",
            "expires_utc": "2026-08-02T00:00:00+00:00",
        }))
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "expired"):
            self.commit(path)
        self.assertEqual(self.write_calls, [])

    def test_edited_plan_fails_the_hash(self):
        path = self.stage()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["payload"]["reference_number"] = "999999"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "hash check failed"):
            self.commit(path)

    def test_resigned_plan_still_fails_semantic_validation(self):
        for mutate, pattern in (
            (lambda raw: raw["payload"].update({"reference_number": "999999"}), "target is not"),
            (lambda raw: raw["selection"].update({"record_id": "96274000009999999"}), "selection does not match"),
            (lambda raw: raw["selection"].update({"target_reference": "5079"}), "selection does not match"),
            (lambda raw: raw["endpoint"].update({"path": "/books/v3/invoices/96274000000312107"}), "one fixed write route"),
            (lambda raw: raw["endpoint"].update({"method": "POST"}), "one fixed write route"),
            (lambda raw: raw.update({"tool": "Some Other Tool"}), "another tool"),
            (lambda raw: raw.update({"tool_version": "9.9.9"}), "another tool"),
            (lambda raw: raw.update({"schema_version": 99}), "another tool"),
            (lambda raw: raw.update({"action": "something_else"}), "another tool"),
            (lambda raw: raw.update({"approval_required": "OK"}), "approval requirement"),
            (lambda raw: raw.update({"record_key": "inv000020-invoice-verify-only"}), "verification-only"),
            (lambda raw: raw.update({"expires_utc": "2026-09-30T00:00:00+00:00"}), "lifetime"),
            (lambda raw: raw["risk"].update({"batch": True}), "risk disclosure"),
            (lambda raw: raw["rendered_before"].update({"proven": False}), "NOT PROVEN"),
            (lambda raw: raw["rendered_before"].update({"displayed_reference": "QT-999"}), "NOT PROVEN"),
            (lambda raw: raw["source_evidence"]["evidence"].update({"subject": "forged"}), "provenance changed"),
        ):
            with self.subTest(pattern=pattern):
                path = self.rewrite(self.stage(), mutate)
                with self.assertRaisesRegex(tool.ClientPoReferenceError, pattern):
                    self.commit(path)
                self.assertEqual(self.write_calls, [])

    def test_stale_plan_refuses_before_any_lock(self):
        plan = self.stage()
        self.bodies["96274000000317001"]["notes"] = "changed by a human in Zoho"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "REFUSED BEFORE LOCK"):
            self.commit(plan)
        self.assertEqual(self.write_calls, [])
        self.assertFalse(self.lock_dir.exists())

    def test_missing_update_scope_refuses_before_lock_and_before_refresh(self):
        plan = self.stage()
        self.refresh_mock.reset_mock()
        self.vault["scopes"] = [tool.SALESORDER_READ_SCOPE, tool.INVOICE_READ_SCOPE]
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "REFUSED BEFORE LOCK"):
            self.commit(plan)
        self.refresh_mock.assert_not_called()
        self.assertFalse(self.lock_dir.exists())
        self.assertEqual(self.write_calls, [])

    def test_a_broad_scope_beside_us_refuses_before_lock(self):
        plan = self.stage()
        self.vault["scopes"] = list(self.vault["scopes"]) + ["ZohoBooks.salesorders.DELETE"]
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "must never run beside"):
            self.commit(plan)
        self.assertEqual(self.write_calls, [])

    def test_success_is_one_locked_put_and_nothing_else_moves(self):
        before = copy.deepcopy(self.bodies)
        plan = self.stage()
        self.rendered_value = None
        output = self.commit(plan)
        self.assertIn("COMMITTED AND VERIFIED", output)
        self.assertEqual(len(self.write_calls), 1)
        call = self.write_calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertTrue(call["lock_exists"], "the lock must exist before the write")
        self.assertEqual(call["payload"], {"customer_id": "96274000000060019", "reference_number": "104662"})
        self.assertIn("/books/v3/salesorders/96274000000317001", call["url"])
        after = self.bodies["96274000000317001"]
        self.assertEqual(after["reference_number"], "104662")
        for field in ("total", "balance", "status", "order_status", "customer_id", "line_items", "notes"):
            self.assertEqual(after[field], before["96274000000317001"][field])
        self.assertEqual(self.bodies["96274000000312107"], before["96274000000312107"])
        lock = next(self.lock_dir.glob("*.json"))
        record = json.loads(lock.read_text())
        self.assertEqual(record["state"], "verified")
        self.assertEqual(record["details"]["emails_sent"], 0)
        self.assertEqual(record["details"]["records_changed"], 1)

    def test_invoice_commit_sends_the_mandatory_line_payload(self):
        self.record_key = "inv000014-invoice"
        plan = self.stage("inv000014-invoice")
        self.commit(plan)
        payload = self.write_calls[0]["payload"]
        self.assertEqual(set(payload), {"customer_id", "reference_number", "line_items"})
        self.assertEqual(len(payload["line_items"]), 1)
        self.assertEqual(payload["line_items"][0]["line_item_id"], "96274000000313007")

    def test_lock_is_exclusive_and_a_second_attempt_never_writes(self):
        plan = self.stage()
        self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "already commit-locked"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_write_failure_locks_indeterminate_and_never_retries(self):
        plan = self.stage()
        self.write_error = HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"code":9,"message":"no"}'))
        with self.assertRaises(tool.ClientPoReferenceError):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")
        self.assertIs(record["details"]["no_retry"], True)
        self.write_error = None
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "already commit-locked"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_readback_reference_mismatch_locks_indeterminate(self):
        plan = self.stage()
        self.mutate_after = lambda body: body.update({"reference_number": "104663"})
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "instead of the approved"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")
        self.assertIs(record["details"]["write_attempted"], True)

    def test_protected_drift_after_write_locks_indeterminate(self):
        classes = {
            "status": lambda body: body.update({"status": "void"}),
            "customer": lambda body: body.update({"customer_id": "96274000009999999"}),
            "number": lambda body: body.update({"salesorder_number": "SO-99999"}),
            "dates": lambda body: body.update({"date": "2026-01-24"}),
            "currency": lambda body: body.update({"currency_code": "USD"}),
            "exchange_rate": lambda body: body.update({"exchange_rate": 1.4}),
            "totals": lambda body: body.update({"total": 999.0}),
            "balance": lambda body: body.update({"balance": 10.0}),
            "taxes": lambda body: body.update({"tax_total": 0.0}),
            "addresses": lambda body: body.update({"billing_address": {"address": "elsewhere"}}),
            "notes": lambda body: body.update({"notes": "rewritten"}),
            "terms": lambda body: body.update({"terms": "Net 60"}),
            "custom_fields": lambda body: body.update({"custom_fields": [{"a": 1}]}),
            "shipping_charge": lambda body: body.update({"shipping_charge": 25.0}),
            "template": lambda body: body.update({"template_id": "999"}),
            "contacts": lambda body: body.update({"contact_persons": ["999"]}),
            "documents": lambda body: body.update({"documents": [{"document_id": "9"}]}),
            "estimate_link": lambda body: body.update({"estimate_id": "999"}),
            "line_quantity": lambda body: body["line_items"][0].update({"quantity": 7.0}),
            "line_rate": lambda body: body["line_items"][0].update({"rate": 60.0}),
            "line_identity": lambda body: body["line_items"][0].update({"line_item_id": "999"}),
            "line_item": lambda body: body["line_items"][0].update({"item_id": "999"}),
            "line_tax": lambda body: body["line_items"][0].update({"tax_id": "999"}),
            "line_description": lambda body: body["line_items"][0].update({"description": "other"}),
            "line_dropped": lambda body: body.update({"line_items": []}),
            "line_added": lambda body: body["line_items"].append(dict(body["line_items"][0])),
        }
        pristine = copy.deepcopy(self.bodies)
        for index, (label, mutate) in enumerate(classes.items(), start=1):
            with self.subTest(protected=label):
                # Each plan carries its own nonce, so it gets its own lock file.
                self.bodies = copy.deepcopy(pristine)
                self.mutate_after = None
                plan = self.stage()
                self.mutate_after = mutate
                with self.assertRaises(tool.ClientPoReferenceError):
                    self.commit(plan)
                self.assertEqual(len(self.write_calls), index)
                lock = tool.lock_path(json.loads(plan.read_text(encoding="utf-8")))
                record = json.loads(lock.read_text())
                self.assertEqual(record["state"], "indeterminate")
                self.assertIs(record["details"]["no_retry"], True)
                self.assertIs(record["details"]["write_attempted"], True)

    def test_dependency_drift_after_write_locks_indeterminate(self):
        plan = self.stage()
        original = tool.get_record

        def drifting(token, domain, org, record):
            body = original(token, domain, org, record)
            if record["record_key"] == "inv000014-invoice" and self.write_calls:
                body = dict(body)
                body["status"] = "void"
            return body

        with mock.patch.object(tool, "get_record", side_effect=drifting):
            with self.assertRaises(tool.ClientPoReferenceError):
                self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_rendered_document_missing_target_locks_indeterminate(self):
        plan = self.stage()
        self.rendered_value = "QT-000012"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "does not show the approved"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")
        self.assertIs(record["details"]["no_retry"], True)

    def test_target_present_elsewhere_but_caption_stale_locks_indeterminate(self):
        """The PO appearing somewhere on the page is NOT proof the field moved."""
        plan = self.stage()
        self.rendered_text_override = (
            "Ref# :\nQT-000012\nNotes\nCustomer PO 104662 was quoted separately.\n"
        )
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "still displays"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")

    def test_rendered_document_without_a_caption_locks_indeterminate(self):
        plan = self.stage()
        self.rendered_value = ""
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "does not show the approved"):
            self.commit(plan)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")

    def test_rendered_fetch_failure_refuses_at_stage(self):
        self.pdf_fail = OSError("network down")
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "indeterminate"):
            self.stage()
        self.assertEqual(self.write_calls, [])

    def test_rendered_extraction_failure_refuses_at_stage(self):
        self.extract_fail = tool.ClientPoReferenceError("PyMuPDF unavailable")
        with self.assertRaises(tool.ClientPoReferenceError):
            self.stage()
        self.assertFalse(list(self.plan_dir.glob("*.json")) if self.plan_dir.exists() else [])

    def test_rendered_extraction_failure_after_write_locks_indeterminate(self):
        plan = self.stage()
        original = tool.rendered_evidence
        calls = {"n": 0}

        def failing(*args, **kwargs):
            calls["n"] += 1
            raise tool.ClientPoReferenceError("text could not be extracted")

        with mock.patch.object(tool, "rendered_evidence", side_effect=failing):
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "extracted"):
                self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        record = json.loads(next(self.lock_dir.glob("*.json")).read_text())
        self.assertEqual(record["state"], "indeterminate")
        self.assertIs(original, tool.rendered_evidence)

    def test_a_non_pdf_body_is_refused(self):
        self.urlopen_mock.side_effect = lambda request, timeout=90: FakeResponse(
            b'{"code":0}', "application/json"
        ) if request.get_method() == "GET" else self.transport(request, timeout)
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "did not return a rendered PDF"):
            self.stage()

    def test_oversize_rendered_document_is_refused(self):
        oversize = b"%PDF-" + b"x" * (tool.MAX_PDF_BYTES + 10)
        self.urlopen_mock.side_effect = lambda request, timeout=90: FakeResponse(
            oversize, "application/pdf"
        ) if request.get_method() == "GET" else self.transport(request, timeout)
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "exceeds the"):
            self.stage()


# ---------------------------------------------------------------------------
# 14, 22, 23 -- route/verb allowlist and containment, proven from the source
# ---------------------------------------------------------------------------


class TestWriteGuard(RepairCase):
    def test_perform_put_refuses_a_record_outside_the_fixed_set(self):
        foreign = dict(tool.WRITABLE_BY_KEY["so00013-order"])
        foreign["record_id"] = "96274000001559012"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "outside the fixed set"):
            tool.perform_put("t", "https://www.zohoapis.ca", ORG_ID, foreign, {})
        self.assertEqual(self.write_calls, [])

    def test_perform_put_refuses_the_verification_only_invoice(self):
        verify_only = dict(tool.VERIFY_ONLY_RECORDS[0])
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.perform_put(
                "t", "https://www.zohoapis.ca", ORG_ID, verify_only,
                {"customer_id": "96274000000060001", "reference_number": "PO5079"},
            )
        self.assertEqual(self.write_calls, [])

    def test_perform_put_refuses_a_widened_or_wrong_body(self):
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        for bad in (
            {"customer_id": "96274000000060019", "reference_number": "104662", "status": "void"},
            {"customer_id": "96274000000060019", "reference_number": "999999"},
            {"customer_id": "96274000000060011", "reference_number": "104662"},
            {"reference_number": "104662"},
        ):
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "REFUSED"):
                tool.perform_put("t", "https://www.zohoapis.ca", ORG_ID, record, bad)
        self.assertEqual(self.write_calls, [])

    def test_get_record_refuses_anything_outside_the_fixed_set(self):
        foreign = dict(tool.WRITABLE_BY_KEY["so00013-order"])
        foreign["record_id"] = "96274000001559012"
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "outside the fixed set"):
            tool.get_record("t", "https://www.zohoapis.ca", ORG_ID, foreign)

    def test_writable_paths_are_exactly_the_twelve(self):
        self.assertEqual(len(tool.WRITABLE_PATHS), 12)
        for path in tool.WRITABLE_PATHS:
            self.assertRegex(path, r"^/books/v3/(salesorders|invoices)/[1-9][0-9]*$")


class TestStaticSurface(unittest.TestCase):
    source = Path(tool.__file__).read_text(encoding="utf-8")

    def test_exactly_one_network_call_site(self):
        tree = ast.parse(self.source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "urlopen"
        ]
        self.assertEqual(len(calls), 1)

    def test_only_get_and_put_verbs_exist(self):
        tree = ast.parse(self.source)
        methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Request":
                for keyword in node.keywords:
                    if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                        methods.add(keyword.value.value)
        self.assertEqual(methods, {"GET", "PUT"})
        lowered = self.source.casefold()
        for forbidden in ('method="post"', 'method="patch"', 'method="delete"', "'post'", "'delete'"):
            self.assertNotIn(forbidden, lowered)

    def test_no_mail_browser_attachment_or_lifecycle_surface(self):
        lowered = self.source.casefold()
        for forbidden in (
            "smtplib", "smtp", "sendmail", "mail.send", "graph.microsoft", "outlook_tool",
            "playwright", "connect_over_cdp", "selenium", "webdriver", "requests.",
            "ui_browser_lock", "/email", "/submit", "/approve", "/reject", "/void",
            "/status/open", "/status/sent", "/status/draft", "/attachment", "/paymentreminder",
            "multipart/form-data", "customerpayments", "creditnotes", "packages", "shipmentorders",
            "ignore_auto_number_generation",
        ):
            self.assertNotIn(forbidden, lowered, f"forbidden surface present: {forbidden}")

    def test_pdf_extraction_is_pymupdf_and_never_shells_out(self):
        """PyMuPDF only. Poppler is named in a comment as excluded, so this
        checks for actual invocation rather than the word."""
        self.assertIn("import fitz", self.source)
        tree = ast.parse(self.source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("subprocess", "shutil", "ctypes", "socket", "smtplib", "webbrowser"):
            self.assertNotIn(forbidden, imported)
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("exec", "eval", "compile", "__import__", "system", "popen", "pdftotext"):
            self.assertNotIn(forbidden, called)

    def test_scopes_are_exactly_the_two_updates(self):
        self.assertIn(tool.SALESORDER_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        self.assertIn(tool.INVOICE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        for forbidden in (
            "ZohoBooks.salesorders.DELETE", "ZohoBooks.invoices.DELETE",
            "ZohoBooks.salesorders.ALL", "ZohoBooks.invoices.ALL",
            "ZohoBooks.fullaccess.all", "ZohoInventory.salesorders.UPDATE",
            "ZohoInventory.invoices.UPDATE",
        ):
            self.assertNotIn(forbidden, zoho_tool.SCOPES)
            with self.assertRaises(zoho_tool.ZohoError):
                zoho_tool.validate_scopes([forbidden])

    def test_connector_still_validates_its_own_scope_list(self):
        zoho_tool.validate_scopes(zoho_tool.SCOPES)

    def test_invoice_revision_tool_still_refuses_paid_invoices(self):
        sibling = (Path(tool.__file__).parent / "zoho_invoice_revision_tool.py").read_text(encoding="utf-8")
        self.assertIn('ALLOWED_STATUSES = ("draft", "sent")', sibling)

    def test_list_fixed_is_read_only(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_list_fixed(argparse.Namespace())
        listing = json.loads(output.getvalue())
        self.assertEqual(listing["writable_records"], 12)
        self.assertEqual(listing["zoho_writes"], 0)
        self.assertTrue(listing["one_record_per_plan"])
        self.assertEqual(len(listing["verification_only"]), 1)
        self.assertFalse(listing["verification_only"][0]["writable"])


# ---------------------------------------------------------------------------
# 24 -- targeted mutations. Each one is a real weakening the suite must catch.
# ---------------------------------------------------------------------------


class TestMutations(TestStaging):
    def assert_caught(self, attribute: str, replacement, action):
        with mock.patch.object(tool, attribute, replacement):
            with self.assertRaises(Exception):
                action()

    def test_tolerant_approval_is_caught(self):
        plan = self.stage()

        def tolerant(args):
            if args.approval.strip().upper() != tool.APPROVAL_WORD:
                raise tool.ClientPoReferenceError("REFUSED: approval must be exactly unpadded.")
            return None

        # The real guard rejects a padded word; a tolerant one would accept it.
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "exactly unpadded"):
            self.commit(plan, " APPROVED ")
        self.assertIsNone(tolerant(argparse.Namespace(approval=" APPROVED ")))

    def test_widened_id_set_is_caught(self):
        widened = tool.WRITABLE_IDS | {"96274000001559012"}
        with mock.patch.object(tool, "WRITABLE_IDS", widened):
            # The path allowlist is a separate, independent gate.
            foreign = dict(tool.WRITABLE_BY_KEY["so00013-order"])
            foreign["record_id"] = "96274000001559012"
            with self.assertRaisesRegex(tool.ClientPoReferenceError, "outside the fixed set"):
                tool.perform_put("t", "https://www.zohoapis.ca", ORG_ID, foreign, {})

    def test_skipped_fingerprint_would_pass_a_mutation_the_real_code_catches(self):
        plan = self.stage()
        self.mutate_after = lambda body: body.update({"notes": "silently rewritten"})
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "Protected fields changed"):
            self.commit(plan)
        # A fingerprint that returns {} for everything would have let it through,
        # which is exactly why the real one keeps every business field.
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        blinded = tool.protected_fingerprint(self.bodies["96274000000317001"], record)
        self.assertIn("notes", blinded)
        self.assertIn("line_items", blinded)
        self.assertNotIn("reference_number", blinded)

    def test_skipped_rendered_check_would_pass_a_case_the_real_code_catches(self):
        plan = self.stage()
        self.rendered_value = "QT-000012"
        with self.assertRaises(tool.ClientPoReferenceError):
            self.commit(plan)
        evidence = {"target_visible": False, "label_present": True, "displayed_reference": "QT-000012"}
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.require_rendered_after(evidence, tool.WRITABLE_BY_KEY["so00013-order"], "104662")

    def test_unlocked_put_is_caught_by_the_lock_before_write_assertion(self):
        plan = self.stage()
        self.commit(plan)
        self.assertTrue(self.write_calls[0]["lock_exists"])
        lock = tool.lock_path(tool.load_plan(str(plan)))
        self.assertTrue(lock.exists())

    def test_second_attempt_is_caught_even_after_an_indeterminate_result(self):
        plan = self.stage()
        self.mutate_after = lambda body: body.update({"notes": "drifted"})
        with self.assertRaises(tool.ClientPoReferenceError):
            self.commit(plan)
        self.mutate_after = None
        with self.assertRaisesRegex(tool.ClientPoReferenceError, "already commit-locked"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_payload_widening_is_caught_at_three_independent_gates(self):
        record = tool.WRITABLE_BY_KEY["so00013-order"]
        widened = {"customer_id": "96274000000060019", "reference_number": "104662", "status": "void"}
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.validate_payload(widened, record, [])
        with self.assertRaises(tool.ClientPoReferenceError):
            tool.perform_put("t", "https://www.zohoapis.ca", ORG_ID, record, widened)
        path = self.rewrite(self.stage(), lambda raw: raw["payload"].update({"status": "void"}))
        with self.assertRaises(tool.ClientPoReferenceError):
            self.commit(path)
        self.assertEqual(self.write_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
