"""Independent safety tests for the SCT PO26330 draft Sales Order tool.

These tests never touch live Zoho and never touch the live vault: every read
transport is patched, and the write transport is patched to fail loudly unless
a test explicitly opts in to a simulated write.

The real client PO on disk is copied into a temporary folder for each test, so
the fixed size and SHA-256 constants are exercised against the genuine bytes
while no test can ever modify the original.
"""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import zoho_sales_order_tool as so_tool
import zoho_tool

HERE = Path(__file__).resolve().parent
SOURCE = so_tool.SOURCE_PDF_PATH

ORGANIZATION = {
    "organization_id": "110002157575",
    "name": "FRP DEPOTS",
    "currency_code": "CAD",
}
NEW_SALESORDER_ID = "96274000001900001"
AUTO_NUMBER = "SO-00042"
PROBE_ID = so_tool.CONTRACT_PROBE_SALESORDER_ID

# The live GST tax this order used BEFORE Rachad's 2026-08-11 Ontario
# correction. It stays in the fixtures as a real neighbouring tax record, so
# the tool has to select its own tax by ID rather than by being handed one.
SUPERSEDED_GST_TAX_ID = "96274000000035512"
# The one plan staged under that superseded build. Never approved, never
# committed, deliberately left on disk as a record.
SUPERSEDED_PLAN = (
    so_tool.ROOT / "Dado" / "20_Working" / "zoho_sales_order_plans"
    / "20260811T175734Z_create_sct_po26330_draft_sales_order_with_attachment"
      "_2950664b01e2366a.json"
)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


def json_response(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def executable_code(path: Path) -> str:
    """The module with every comment and docstring removed.

    Containment tests must be about what the code CAN DO, not about the prose
    describing what it deliberately cannot. Scanning the raw file for a token
    like "outlook" or "/approve" only ever proves the docstring mentions it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def fake_contact(**overrides: object) -> dict:
    contact = {
        "contact_id": so_tool.CUSTOMER_ID,
        "contact_name": so_tool.CUSTOMER_NAME,
        "company_name": so_tool.CUSTOMER_NAME,
        "status": "active",
        "contact_type": "customer",
        "currency_code": "CAD",
        "currency_id": "96274000000000097",
        "payment_terms": -3,
        "payment_terms_label": "Due end of next month",
        "billing_address": {
            "address_id": so_tool.BILLING_ADDRESS_ID,
            "address": "200-100 Hoka St",
            "city": "Winnipeg",
            "zip": "R2C 3N2",
            "country": "Canada",
        },
        "shipping_address": {
            "address_id": so_tool.SHIPPING_ADDRESS_ID,
            "address": "200-100 Hoka St",
            "city": "Winnipeg",
            "zip": "R2C 3N2",
            "country": "Canada",
        },
        "contact_persons": [
            {
                "contact_person_id": "96274000000186535",
                "first_name": "",
                "last_name": "",
                "email": "bzadro@sctfrp.com",
            },
            {
                "contact_person_id": so_tool.CONTACT_PERSON_ID,
                "first_name": "Bon",
                "last_name": "Bacani",
                "email": "bbacani@sctfrp.com",
            },
        ],
    }
    contact.update(overrides)
    return contact


def fake_item(**overrides: object) -> dict:
    item = {
        "item_id": so_tool.ITEM_ID,
        "name": so_tool.ITEM_NAME,
        "sku": so_tool.ITEM_SKU,
        "status": "active",
        "unit": so_tool.ITEM_UNIT,
        "rate": 45.72,
        "purchase_rate": 25.0,
        "stock_on_hand": 2.0,
        "available_stock": 2.0,
        "actual_available_stock": 2.0,
    }
    item.update(overrides)
    return item


def fake_probe(**overrides: object) -> dict:
    """A real existing order, trimmed to what the contract probe reads."""
    probe = {
        "salesorder_id": PROBE_ID,
        "salesorder_number": so_tool.CONTRACT_PROBE_SALESORDER_NUMBER,
        "customer_id": so_tool.CUSTOMER_ID,
        "status": "invoiced",
        "reference_number": "QT-000023",
        "date": "2026-05-27",
        "payment_terms": -3,
        "payment_terms_label": "Due end of next month",
        "shipment_date": "2026-06-02",
        "contact_persons": ["96274000000186535"],
        "documents": [],
        "notes": "Looking forward for your business.",
        "terms": "",
    }
    probe.update(overrides)
    return probe


def historical_so_00020() -> dict:
    """CAD 105.42, no PO reference, the now-legacy item.

    That total was this order's own total under the superseded GST-5% build, so
    it is exactly the coincidence the duplicate sweep must not fire on. It is
    deliberately present in every fixture: it must never be treated as a
    duplicate and must never be touched. A money total is never a match
    criterion, which is why the Ontario correction does not change that.
    """
    return {
        "salesorder_id": "96274000000508340",
        "salesorder_number": "SO-00020",
        "customer_id": so_tool.CUSTOMER_ID,
        "status": "invoiced",
        "reference_number": "",
        "date": "2026-02-26",
        "total": 105.42,
        "notes": "",
        "terms": "",
    }


class SalesOrderTestCase(unittest.TestCase):
    """Shared harness. No live Zoho call and no live file write can escape it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "zoho_sales_order_plans"
        self.plan_dir.mkdir()
        self.pdf_path = self.root / so_tool.SOURCE_PDF_NAME
        shutil.copyfile(SOURCE, self.pdf_path)

        self.contact = fake_contact()
        self.item = fake_item()
        self.taxes = [
            {
                "tax_id": so_tool.TAX_ID,
                "tax_name": so_tool.TAX_NAME,
                "tax_percentage": 13.0,
                "tax_type": "tax",
            },
            {"tax_id": SUPERSEDED_GST_TAX_ID, "tax_name": "GST", "tax_percentage": 5.0},
            {"tax_id": "96274000000035514", "tax_name": "QST", "tax_percentage": 9.975},
        ]
        self.probe = fake_probe()
        self.salesorder_rows = [
            historical_so_00020(),
            {
                "salesorder_id": PROBE_ID,
                "salesorder_number": "SO-00041",
                "customer_id": so_tool.CUSTOMER_ID,
                "status": "invoiced",
                "reference_number": "QT-000023",
            },
        ]
        self.salesorder_details = {
            PROBE_ID: self.probe,
            "96274000000508340": historical_so_00020(),
        }
        self.page_context = {"has_more_page": False}
        self.get_paths: list[str] = []
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": ORGANIZATION["organization_id"],
            "inventory_organization_id": ORGANIZATION["organization_id"],
            "scopes": list(so_tool.REQUIRED_READ_SCOPES) + [so_tool.CREATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(so_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(so_tool, "SOURCE_PDF_PATH", self.pdf_path),
            mock.patch.object(so_tool.zoho_tool, "load_vault", side_effect=self.load_vault),
            mock.patch.object(
                so_tool.zoho_tool,
                "refresh_access_token",
                side_effect=lambda vault=None: ("token", self.vault),
            ),
            mock.patch.object(so_tool.zoho_tool, "save_vault"),
            mock.patch.object(so_tool.zoho_tool, "append_receipt"),
            mock.patch.object(so_tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(
                so_tool,
                "urlopen",
                side_effect=AssertionError("a live Zoho write is forbidden in tests"),
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.save_vault = started[4]
        self.append_receipt = started[5]
        self.api_get_mock = started[6]
        self.urlopen = started[7]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    # -- fake read transport ---------------------------------------------
    def load_vault(self) -> dict:
        return self.vault

    def api_get(self, access_token: str, api_domain: str, path: str) -> dict:
        self.get_paths.append(path)
        route = path.split("?", 1)[0]
        if route == "/books/v3/organizations":
            return {"organizations": [copy.deepcopy(ORGANIZATION)]}
        if route == "/books/v3/settings/taxes":
            return {"taxes": copy.deepcopy(self.taxes)}
        match = re.fullmatch(r"/books/v3/contacts/([0-9]+)(/address)?", route)
        if match:
            if match.group(1) != so_tool.CUSTOMER_ID:
                return {"contact": None, "addresses": []}
            if match.group(2):
                return {"addresses": [
                    copy.deepcopy(self.contact["billing_address"]),
                    copy.deepcopy(self.contact["shipping_address"]),
                ]}
            return {"contact": copy.deepcopy(self.contact)}
        match = re.fullmatch(r"/inventory/v1/items/([0-9]+)", route)
        if match:
            item = self.item if match.group(1) == str(self.item.get("item_id")) else None
            return {"item": copy.deepcopy(item) if item else None}
        match = re.fullmatch(r"/books/v3/salesorders/([0-9]+)", route)
        if match:
            record = self.salesorder_details.get(match.group(1))
            return {"salesorder": copy.deepcopy(record) if record else None}
        if route == so_tool.SALESORDER_COLLECTION_PATH:
            return {
                "salesorders": copy.deepcopy(self.salesorder_rows),
                "page_context": copy.deepcopy(self.page_context),
            }
        raise AssertionError(f"unexpected GET {path}")

    # -- helpers ---------------------------------------------------------
    def lock_files(self) -> list[str]:
        lock_dir = self.plan_dir / ".commit-locks"
        return sorted(path.name for path in lock_dir.glob("*.json")) if lock_dir.exists() else []

    def stage(self) -> Path:
        so_tool.command_stage(mock.Mock())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        return plans[0]

    def plan_body(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rewrite_plan(self, path: Path, mutate) -> Path:
        plan = self.plan_body(path)
        mutate(plan)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def reseal_plan(self, path: Path, mutate) -> Path:
        """Mutate a plan AND repair its hash: proves the checks below the hash."""
        plan = self.plan_body(path)
        mutate(plan)
        core = {key: value for key, value in plan.items() if key != "sha256"}
        plan["sha256"] = so_tool.digest_for(core)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def created_salesorder(self, payload: dict, **overrides: object) -> dict:
        """The live order Zoho would hold after this exact payload."""
        line = payload["line_items"][0]
        quantity = Decimal(str(line["quantity"]))
        rate = Decimal(str(line["rate"]))
        sub_total = (quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_total = (sub_total * Decimal(so_tool.TAX_PERCENTAGE) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        record = {
            "salesorder_id": NEW_SALESORDER_ID,
            "salesorder_number": AUTO_NUMBER,
            "status": "draft",
            "customer_id": payload["customer_id"],
            "customer_name": so_tool.CUSTOMER_NAME,
            "reference_number": payload["reference_number"],
            "date": payload["date"],
            "currency_code": "CAD",
            "exchange_rate": 1.0,
            "payment_terms": payload["payment_terms"],
            "payment_terms_label": payload["payment_terms_label"],
            "notes": payload["notes"],
            "terms": "",
            "is_emailed": False,
            "billing_address": {"address_id": payload["billing_address_id"]},
            "shipping_address": {"address_id": payload["shipping_address_id"]},
            "shipping_charge": 0.0,
            "adjustment": 0.0,
            "is_inclusive_tax": False,
            "sub_total": float(sub_total),
            "tax_total": float(tax_total),
            "total": float(sub_total + tax_total),
            "documents": [],
            "line_items": [{
                "line_item_id": "96274000001900010",
                "item_id": line["item_id"],
                "name": so_tool.ITEM_NAME,
                "sku": so_tool.ITEM_SKU,
                "unit": so_tool.ITEM_UNIT,
                "description": line["description"],
                "quantity": float(quantity),
                "rate": float(rate),
                "discount": 0.0,
                "tax_id": line["tax_id"],
                "tax_percentage": float(so_tool.TAX_PERCENTAGE),
                "item_total": float(sub_total),
            }],
        }
        if "shipment_date" in payload:
            record["shipment_date"] = payload["shipment_date"]
        if "contact_persons" in payload:
            record["contact_persons"] = list(payload["contact_persons"])
        record.update(overrides)
        return record

    def allow_writes(
        self,
        *,
        on_create=None,
        on_attach=None,
        attachment_bytes=None,
        register=True,
    ):
        """Opt in to a SIMULATED write transport. Still no network."""
        state: dict[str, object] = {}

        def transport(request, timeout=120):
            url = request.full_url
            method = request.get_method()
            route = url.split("?", 1)[0].split("zohoapis.ca", 1)[-1]
            record = {
                "url": url,
                "method": method,
                "route": route,
                "locks_before": self.lock_files(),
                "content_type": request.headers.get("Content-type", ""),
                "body": request.data,
            }
            self.write_calls.append(record)
            if method == "GET" and route.endswith("/attachment"):
                if attachment_bytes is not None:
                    return FakeResponse(attachment_bytes)
                return FakeResponse(self.pdf_path.read_bytes())
            if method == "POST" and route == so_tool.SALESORDER_COLLECTION_PATH:
                payload = json.loads(request.data.decode("utf-8"))
                record["payload"] = payload
                if on_create is not None:
                    result = on_create(record)
                    if result is not None:
                        return json_response(result)
                created = self.created_salesorder(payload, **state.get("create_overrides", {}))
                if register:
                    self.salesorder_details[NEW_SALESORDER_ID] = created
                    self.salesorder_rows.append({
                        "salesorder_id": NEW_SALESORDER_ID,
                        "salesorder_number": AUTO_NUMBER,
                        "customer_id": created["customer_id"],
                        "status": created["status"],
                        "reference_number": created["reference_number"],
                    })
                return json_response({
                    "code": 0,
                    "message": "The sales order has been created.",
                    "salesorder": {"salesorder_id": NEW_SALESORDER_ID},
                })
            if method == "POST" and route.endswith("/attachment"):
                if on_attach is not None:
                    result = on_attach(record)
                    if result is not None:
                        return json_response(result)
                # Zoho stores the file and the order then lists it.
                stored = self.salesorder_details.get(NEW_SALESORDER_ID)
                if isinstance(stored, dict) and stored.get("documents") == []:
                    stored["documents"] = [{
                        "document_id": "96274000001900020",
                        "file_name": so_tool.SOURCE_PDF_NAME,
                        "file_type": "pdf",
                        "file_size": so_tool.SOURCE_PDF_SIZE,
                        "attachment_order": 0,
                    }]
                return json_response({"code": 0, "message": "Your file has been attached."})
            raise AssertionError(f"unexpected write {method} {url}")

        self.urlopen.side_effect = transport
        return state

    def commit(self, plan_path: Path, approval: str = so_tool.APPROVAL_WORD) -> None:
        # His go is timed one minute AFTER the plan was written (A3/A5:
        # --approval-message-utc is required on every money commit).
        created = json.loads(plan_path.read_text(encoding="utf-8"))["created_utc"]
        so_tool.command_commit(
            mock.Mock(
                plan=str(plan_path), approval=approval, approval_lane=None,
                approval_message_utc=(datetime.fromisoformat(created) + timedelta(minutes=1)).isoformat(),
            )
        )

    def assert_no_write_attempted(self) -> None:
        self.assertEqual(self.write_calls, [])
        self.assertEqual(self.lock_files(), [])

    def reset(self) -> None:
        """A clean harness inside a sub-test, without leaking patchers."""
        self.tearDown()
        self.setUp()


# ---------------------------------------------------------------------------
# The exact commissioned surface
# ---------------------------------------------------------------------------


class SurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(so_tool.__file__)
        self.source = self.path.read_text(encoding="utf-8")
        # Comment- and docstring-free, so these assertions are about the code.
        self.code = executable_code(self.path)
        # These three tuples are declarative lists the tool reads AGAINST, not
        # things it can do: fields it refuses to send, scopes it refuses to
        # hold, and the live order states it classifies during the duplicate
        # sweep. A token appearing in them is the opposite of a capability, so
        # the containment scans below look at the code without them.
        self.code_without_refusals = self.code
        for name in (
            "REFUSED_POST_KEYS", "FORBIDDEN_SALESORDER_SCOPES", "OPEN_SALESORDER_STATUSES",
        ):
            stripped = re.sub(
                rf"{name} = \(.*?\)\n", "", self.code_without_refusals, flags=re.S
            )
            self.assertNotEqual(stripped, self.code_without_refusals, name)
            self.code_without_refusals = stripped

    def test_the_real_client_po_matches_the_fixed_size_and_hash(self) -> None:
        content = so_tool.read_source_pdf()
        self.assertEqual(len(content), 149315)
        self.assertEqual(
            hashlib.sha256(content).hexdigest(),
            "5703f691a7cf278b9fc93930b1ffacebb290f392fbb1741ed22b82b561f4f2ef",
        )
        self.assertEqual(so_tool.SOURCE_PDF_SIZE, len(content))

    def test_no_update_or_delete_verb_exists_anywhere(self) -> None:
        for verb in ("PUT", "PATCH", "DELETE", "HEAD"):
            self.assertIsNone(re.search(rf"\b{verb}\b", self.code_without_refusals), verb)
        self.assertEqual(so_tool.ALLOWED_METHODS, ("GET", "POST"))

    def test_exactly_one_network_call_site(self) -> None:
        self.assertEqual(self.code.count("urlopen("), 1)
        self.assertEqual(len(re.findall(r"\burlopen\b", self.code)), 2)  # import + call

    def test_no_mail_transport_of_any_kind(self) -> None:
        lowered = self.code_without_refusals.casefold()
        for token in (
            "smtplib", "smtp", "sendmail", "send_mail", "to_mail_ids", "cc_mail_ids",
            "bcc_mail_ids", "graph.microsoft", "outlook", "mailto:", "/email",
            "email_address", "recipient", "can_send_in_mail", "notification",
        ):
            self.assertNotIn(token, lowered, token)
        # Each of those mail parameters exists ONLY as a refused key.
        for token in ("can_send_in_mail", "to_mail_ids", "cc_mail_ids", "bcc_mail_ids"):
            self.assertIn(token, so_tool.REFUSED_POST_KEYS, token)
        # "email_sent": False is a disclosure, never a capability.
        self.assertIn("'email_sent': False", self.code)

    def test_no_browser_or_cdp_path(self) -> None:
        lowered = self.code.casefold()
        for token in (
            "connect_over_cdp", "playwright", "9228", "9229", "webdriver", "selenium",
            "cookie", "csrf", "subprocess",
        ):
            self.assertNotIn(token, lowered, token)

    def test_no_lifecycle_or_dependent_route(self) -> None:
        """Only three routes are constructible, and the code proves it."""
        routes = sorted(set(
            re.findall(r"/(?:books|inventory)/v[0-9]+/[A-Za-z0-9_{}/]*", self.code)
        ))
        self.assertEqual(routes, sorted([
            # reads
            "/books/v3/contacts/{CUSTOMER_ID}",
            "/books/v3/contacts/{CUSTOMER_ID}/address",
            "/books/v3/organizations",
            "/books/v3/salesorders",                      # list, and the create POST
            "/books/v3/salesorders/",                     # the ID-suffixed read prefix
            "/books/v3/salesorders/{salesorder_id}",
            "/books/v3/settings/taxes",
            "/inventory/v1/items/{ITEM_ID}",
            # the one attachment route, and the plan's description of it
            "/books/v3/salesorders/{new_salesorder_id}/attachment",
            "/books/v3/salesorders/{salesorder_id}/attachment",
        ]))
        # No route carries a lifecycle, dependent-record or mail segment. This
        # is asserted on the ROUTES themselves rather than on the source text,
        # because the refusal messages legitimately name what they refuse.
        for route in routes:
            for segment in (
                "status", "confirm", "approve", "void", "convert", "package", "shipment",
                "invoice", "template", "email", "mail", "reminder", "comment", "bulk",
                "submit", "inventoryadjustment", "delete",
            ):
                self.assertNotIn(segment, route.casefold(), f"{segment} in {route}")

    def test_only_the_attachment_route_is_write_reachable_beyond_the_collection(self) -> None:
        self.assertTrue(so_tool.ATTACHMENT_PATH_RE.fullmatch(
            "/books/v3/salesorders/123/attachment"
        ))
        for path in (
            "/books/v3/salesorders/123",
            "/books/v3/salesorders/123/attachment/456",
            "/books/v3/salesorders/0/attachment",
            "/books/v3/salesorders//attachment",
        ):
            self.assertIsNone(so_tool.ATTACHMENT_PATH_RE.fullmatch(path), path)

    def test_zoho_assigns_the_order_number(self) -> None:
        # The only mention in executable code is the refused-key list.
        self.assertEqual(self.code.count("ignore_auto_number_generation"), 1)
        self.assertIn("ignore_auto_number_generation", so_tool.REFUSED_POST_KEYS)
        self.assertNotIn("salesorder_number", so_tool.ALLOWED_POST_KEYS)
        self.assertIn("salesorder_number", so_tool.REFUSED_POST_KEYS)

    def test_only_the_one_create_scope_is_used(self) -> None:
        self.assertEqual(so_tool.CREATE_SCOPE, "ZohoBooks.salesorders.CREATE")
        self.assertIn(so_tool.CREATE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        # *** THIS COMMISSION'S GUARD NOW CONFLICTS WITH A LATER ONE, DELIBERATELY. ***
        # On 2026-08-12 ZohoBooks.salesorders.UPDATE was commissioned for
        # zoho_historical_client_po_reference_tool.py, so it IS in the prepared
        # connector scope list. This module still lists it as forbidden FOR
        # ITSELF and still refuses to run beside it -- unchanged and deliberate.
        # The consequence is stated rather than hidden: once Rachad reauthorizes
        # with the new scope, this SCT PO26330 tool refuses at staging until
        # Rachad decides how to resolve it. Nothing here weakens either guard.
        self.assertIn("ZohoBooks.salesorders.UPDATE", so_tool.FORBIDDEN_SALESORDER_SCOPES)
        self.assertIn("ZohoBooks.salesorders.UPDATE", zoho_tool.SCOPES)
        for forbidden in so_tool.FORBIDDEN_SALESORDER_SCOPES:
            if forbidden == "ZohoBooks.salesorders.UPDATE":
                continue
            self.assertNotIn(forbidden, zoho_tool.SCOPES)
        self.assertTrue(all(scope.endswith(".READ") for scope in so_tool.REQUIRED_READ_SCOPES))

    def test_approval_is_his_unambiguous_go_after_the_plan(self) -> None:
        """2026-08-21 (A3): APPROVED still works and is still the word the plan
        names; "yes go ahead" to the shown plan counts; a condition, a question,
        a blank or a non-string refuses; a word sent before the plan existed
        cannot approve it."""
        self.assertEqual(so_tool.APPROVAL_WORD, "APPROVED")
        plan = {"created_utc": "2026-08-11T10:00:00+00:00", "expires_utc": "2026-08-12T10:00:00+00:00"}
        after = "2026-08-11T10:01:00+00:00"
        for bad in ("approved but wait", "hold", "APPROVED?", "APPROVED\nnow", "", None, 1):
            with self.assertRaises(so_tool.SalesOrderError):
                so_tool.require_rachad_approval(bad, plan, sent_utc=after)
        self.assertTrue(so_tool.require_rachad_approval("APPROVED", plan, sent_utc=after).exact_word)
        self.assertEqual(so_tool.require_rachad_approval("yes go ahead", plan, sent_utc=after).kind, "money")
        # A go with no time is refused: "after the plan" cannot be proven.
        with self.assertRaisesRegex(so_tool.SalesOrderError, "--approval-message-utc"):
            so_tool.require_rachad_approval("APPROVED", plan)
        with self.assertRaisesRegex(so_tool.SalesOrderError, "BEFORE this plan was created"):
            so_tool.require_rachad_approval("APPROVED", plan, sent_utc="2026-08-11T09:59:00+00:00")
        go = so_tool.require_rachad_approval("APPROVED", plan, sent_utc=after)
        self.assertEqual(go.sent_utc, after)
        # The plan is mandatory: there is no reversible fallback on a money check.
        with self.assertRaises(TypeError):
            so_tool.require_rachad_approval("APPROVED")

    def test_the_fixed_transaction_constants_match_the_client_po(self) -> None:
        self.assertEqual(so_tool.CUSTOMER_ID, "96274000000186533")
        self.assertEqual(so_tool.ITEM_ID, "96274000000523055")
        self.assertEqual(so_tool.PO_REFERENCE, "PO26330")
        self.assertEqual(so_tool.ORDER_DATE, "2026-08-11")
        self.assertEqual(so_tool.REQUIRED_DATE, "2026-08-12")
        self.assertEqual((so_tool.PAYMENT_TERMS, so_tool.PAYMENT_TERMS_LABEL), (30, "Net 30"))
        self.assertEqual((so_tool.QUANTITY, so_tool.RATE), ("2", "50.20"))
        self.assertEqual(so_tool.REQUIRED_PHYSICAL_STOCK, Decimal("2"))
        for fragment in (
            "Tag: SO38211-Nutrien Vanscoy",
            "Required: 2026-08-12",
            "Ship via Purolator Express collect on SCT account 3763800",
            "Please send tracking number once shipped.",
        ):
            self.assertIn(fragment, so_tool.NOTES)

    def test_the_tax_matches_the_revised_client_po_ontario_hst(self) -> None:
        """The revised PO and Rachad's correction both require Ontario HST."""
        self.assertEqual(so_tool.TAX_ID, "96274000000035516")
        self.assertEqual(so_tool.TAX_NAME, "ON HST")
        self.assertEqual(so_tool.TAX_PERCENTAGE, "13")
        # The superseded GST tax is not reachable: it is not the fixed ID.
        self.assertNotEqual(so_tool.TAX_ID, SUPERSEDED_GST_TAX_ID)
        self.assertNotIn(SUPERSEDED_GST_TAX_ID, self.code)
        self.assertEqual(so_tool.CLIENT_PO_TAX_LABEL, "HST - ON@13.0%")
        self.assertEqual(so_tool.CLIENT_PO_TAX_TOTAL, "13.05")
        source = so_tool.VALUE_SOURCES["tax"]
        self.assertIn("PO26330 Rev.1", source)
        self.assertIn("matching", source)

    def test_look_alike_items_are_named_and_refused(self) -> None:
        self.assertEqual(
            set(so_tool.REFUSED_ITEM_IDS), {"96274000000523057", "96274000000508303"}
        )
        for item_id in so_tool.REFUSED_ITEM_IDS:
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.item_evidence(fake_item(item_id=item_id))
            self.assertIn("REFUSED", str(caught.exception))

    def test_independent_totals_use_ontario_hst(self) -> None:
        totals = so_tool.build_totals()
        # Sub-total is the client PO's own; tax and total are Ontario HST 13%.
        self.assertEqual(totals["sub_total"], "100.40")
        self.assertEqual(totals["tax_total"], "13.05")
        self.assertEqual(totals["total"], "113.45")
        self.assertEqual(totals["tax_percentage"], "13")
        self.assertTrue(totals["deterministic"])
        # 100.40 x 13% = 13.052, half-up to two decimals.
        self.assertEqual(
            (Decimal("100.40") * Decimal(13) / Decimal(100)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            Decimal("13.05"),
        )
        self.assertIn("ON HST at 13%", totals["basis"])

    def test_totals_refuse_a_drifted_constant(self) -> None:
        with mock.patch.object(so_tool, "RATE", "50.21"):
            with self.assertRaises(so_tool.SalesOrderError):
                so_tool.build_totals()
        with mock.patch.object(so_tool, "EXPECTED_TAX_TOTAL", "13.06"):
            with self.assertRaises(so_tool.SalesOrderError):
                so_tool.build_totals()
        # The superseded GST figures no longer reconcile with anything.
        with mock.patch.object(so_tool, "EXPECTED_TAX_TOTAL", "5.02"):
            with mock.patch.object(so_tool, "EXPECTED_TOTAL", "105.42"):
                with self.assertRaises(so_tool.SalesOrderError):
                    so_tool.build_totals()

    def test_the_risk_note_discloses_the_non_atomic_pair(self) -> None:
        self.assertIn("NOT atomic", so_tool.RISK_NOTE)
        self.assertIn("WITHOUT ITS ATTACHMENT", so_tool.RISK_NOTE)
        self.assertIn("NEITHER ROUTE WAS EXERCISED LIVE", so_tool.ATTACHMENT_CONTRACT_NOTE)


class PayloadShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = so_tool.contract_evidence(fake_probe())
        self.payload = so_tool.build_payload(self.contract)

    def test_payload_is_exactly_the_commissioned_shape(self) -> None:
        self.assertEqual(set(self.payload) - so_tool.ALLOWED_POST_KEYS, set())
        self.assertTrue(so_tool.REQUIRED_POST_KEYS.issubset(self.payload))
        self.assertEqual(len(self.payload["line_items"]), 1)
        self.assertEqual(
            sorted(self.payload["line_items"][0]), sorted(so_tool.LINE_POST_KEYS)
        )

    def test_every_refused_field_is_rejected(self) -> None:
        for key in so_tool.REFUSED_POST_KEYS:
            payload = dict(self.payload)
            payload[key] = "x"
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.require_post_shape(payload)
            self.assertIn("REFUSED", str(caught.exception))

    def test_wrong_values_are_rejected(self) -> None:
        mutations = [
            ("customer_id", "96274000000999999"),
            ("reference_number", "PO26331"),
            ("date", "2026-08-12"),
            ("shipment_date", "2026-08-13"),
            ("payment_terms", 15),
            ("payment_terms_label", "Net 15"),
            ("billing_address_id", "96274000000186539"),
            ("shipping_address_id", "96274000000186539"),
            ("notes", "Tag: SO38211-Nutrien Vanscoy"),
            ("contact_persons", ["96274000000186535"]),
        ]
        for key, value in mutations:
            payload = copy.deepcopy(self.payload)
            payload[key] = value
            with self.assertRaises(so_tool.SalesOrderError, msg=key):
                so_tool.require_post_shape(payload)

    def test_wrong_line_values_are_rejected(self) -> None:
        for key, value in (
            ("item_id", "96274000000523057"),
            ("item_id", "96274000000508303"),
            ("quantity", 3.0),
            ("rate", 45.72),
            ("tax_id", "96274000000035514"),
            ("description", "Coupling"),
        ):
            payload = copy.deepcopy(self.payload)
            payload["line_items"][0][key] = value
            with self.assertRaises(so_tool.SalesOrderError, msg=f"{key}={value}"):
                so_tool.require_post_shape(payload)

    def test_a_second_line_is_refused(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["line_items"].append(copy.deepcopy(payload["line_items"][0]))
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.require_post_shape(payload)
        payload["line_items"] = []
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.require_post_shape(payload)

    def test_unproven_optional_fields_are_omitted_not_guessed(self) -> None:
        contract = so_tool.contract_evidence(
            fake_probe(**{"shipment_date": None, "contact_persons": None})
        )
        # Explicit nulls still count as exposed keys; remove them entirely.
        raw = fake_probe()
        raw.pop("shipment_date")
        raw.pop("contact_persons")
        contract = so_tool.contract_evidence(raw)
        self.assertFalse(contract["shipment_date_supported"])
        self.assertFalse(contract["contact_persons_supported"])
        payload = so_tool.build_payload(contract)
        self.assertNotIn("shipment_date", payload)
        self.assertNotIn("contact_persons", payload)
        self.assertIn("the fixed notes ONLY", contract["required_date_placement"])
        # The required date is still recorded, in the notes.
        self.assertIn("Required: 2026-08-12", payload["notes"])

    def test_missing_payment_terms_contract_refuses_to_stage(self) -> None:
        raw = fake_probe()
        raw.pop("payment_terms")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.contract_evidence(raw)
        self.assertIn("Net 30", str(caught.exception))

    def test_probe_must_be_the_fixed_existing_order(self) -> None:
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.contract_evidence(fake_probe(salesorder_id="96274000000253041"))
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.contract_evidence(fake_probe(salesorder_number="SO-00040"))


class AttachmentTransportTests(unittest.TestCase):
    def test_attachment_route_only_accepts_the_just_created_id(self) -> None:
        content = so_tool.read_source_pdf()
        for path, created in (
            ("/books/v3/salesorders/96274000000508340/attachment", NEW_SALESORDER_ID),
            (f"/books/v3/salesorders/{NEW_SALESORDER_ID}/attachment", "96274000000508340"),
        ):
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.oauth_salesorder_attachment_write_allowed(
                    "token", "https://www.zohoapis.ca", "POST", path, "1", created, content
                )
            self.assertIn("REFUSED", str(caught.exception))

    def test_attachment_route_shape_is_fixed(self) -> None:
        content = so_tool.read_source_pdf()
        for path in (
            "/books/v3/salesorders/attachment",
            f"/books/v3/salesorders/{NEW_SALESORDER_ID}",
            f"/books/v3/salesorders/{NEW_SALESORDER_ID}/attachment/1",
            f"/books/v3/invoices/{NEW_SALESORDER_ID}/attachment",
            f"/books/v3/salesorders/0/attachment",
        ):
            with self.assertRaises(so_tool.SalesOrderError):
                so_tool.oauth_salesorder_attachment_write_allowed(
                    "token", "https://www.zohoapis.ca", "POST", path, "1",
                    NEW_SALESORDER_ID, content,
                )

    def test_only_the_exact_pdf_bytes_may_be_uploaded(self) -> None:
        for content in (b"%PDF-1.4 not the PO", so_tool.read_source_pdf() + b"x"):
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.oauth_salesorder_attachment_write_allowed(
                    "token", "https://www.zohoapis.ca", "POST",
                    f"/books/v3/salesorders/{NEW_SALESORDER_ID}/attachment", "1",
                    NEW_SALESORDER_ID, content,
                )
            self.assertIn("not the exact client PO", str(caught.exception))

    def test_multipart_body_carries_one_named_file_part(self) -> None:
        content = so_tool.read_source_pdf()
        body, boundary = so_tool.build_multipart_body(content)
        self.assertNotIn(boundary.encode("ascii"), content)
        self.assertEqual(body.count(boundary.encode("ascii")), 2)
        header = body.split(b"\r\n\r\n", 1)[0].decode("utf-8")
        self.assertIn('name="attachment"', header)
        self.assertIn(f'filename="{so_tool.SOURCE_PDF_NAME}"', header)
        self.assertIn("Content-Type: application/pdf", header)
        self.assertIn(content, body)

    def test_only_get_and_post_are_issuable(self) -> None:
        for method in ("PUT", "PATCH", "DELETE", "HEAD", "put"):
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool._perform(method, "https://www.zohoapis.ca/x", {}, None, "probe")
            self.assertIn("REFUSED", str(caught.exception))

    def test_create_route_is_the_collection_only(self) -> None:
        contract = so_tool.contract_evidence(fake_probe())
        payload = so_tool.build_payload(contract)
        for path in (
            f"/books/v3/salesorders/{NEW_SALESORDER_ID}",
            "/books/v3/salesorders/",
            "/books/v3/invoices",
            f"/books/v3/salesorders/{NEW_SALESORDER_ID}/status/confirmed",
        ):
            with self.assertRaises(so_tool.SalesOrderError):
                so_tool.oauth_salesorder_create_write_allowed(
                    "token", "https://www.zohoapis.ca", "POST", path, "1", payload
                )


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


class StageTests(SalesOrderTestCase):
    def test_stage_writes_a_plan_and_performs_zero_zoho_writes(self) -> None:
        path = self.stage()
        plan = self.plan_body(path)
        self.assertEqual(plan["action"], so_tool.ACTION)
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertEqual(plan["risk"]["writes"], 2)
        self.assertFalse(plan["risk"]["atomic"])
        self.assertFalse(plan["risk"]["email_sent"])
        self.assertEqual(plan["live_evidence"]["totals"]["total"], "113.45")
        self.assertEqual(plan["live_evidence"]["tax"]["tax_id"], so_tool.TAX_ID)
        # Stored as Zoho's own rendering of the live rate, so compare the value.
        self.assertEqual(
            Decimal(plan["live_evidence"]["tax"]["tax_percentage"]), Decimal("13")
        )
        self.assertEqual(plan["live_evidence"]["totals"]["tax_percentage"], "13")
        self.assertEqual(plan["schema_version"], 4)
        self.assertEqual(plan["tool_version"], "2.2.0")
        self.assertEqual(
            plan["live_evidence"]["attachment"]["sha256"], so_tool.SOURCE_PDF_SHA256
        )
        self.urlopen.assert_not_called()
        self.assertEqual(self.lock_files(), [])
        receipt = self.append_receipt.call_args[0]
        self.assertIn("plan_staged_not_committed", receipt[0])
        self.assertIn("zoho_writes=0", receipt[1])
        self.assertIn("attachments_uploaded=0", receipt[1])
        self.assertIn("email_sent=false", receipt[1])

    def test_stage_never_requires_the_write_scope(self) -> None:
        self.vault["scopes"] = list(so_tool.REQUIRED_READ_SCOPES)
        path = self.stage()
        self.assertTrue(path.is_file())

    def test_stage_refuses_without_the_read_scopes(self) -> None:
        self.vault["scopes"] = ["ZohoBooks.contacts.READ"]
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("READ scope", str(caught.exception))
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_stage_refuses_a_forbidden_scope_in_the_vault(self) -> None:
        self.vault["scopes"].append("ZohoBooks.salesorders.DELETE")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("never authorized", str(caught.exception))

    def test_stage_reads_each_dependency_twice_and_refuses_unstable_state(self) -> None:
        self.stage()
        item_reads = [path for path in self.get_paths if "/inventory/v1/items/" in path]
        self.assertGreaterEqual(len(item_reads), 2)

    def test_unstable_live_state_is_refused(self) -> None:
        calls = {"n": 0}
        original = self.item

        def drifting(access_token, api_domain, path):
            if "/inventory/v1/items/" in path:
                calls["n"] += 1
                item = copy.deepcopy(original)
                if calls["n"] > 1:
                    item["actual_available_stock"] = 5.0
                return {"item": item}
            return self.api_get(access_token, api_domain, path)

        self.api_get_mock.side_effect = drifting
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("two consecutive live reads", str(caught.exception))

    # -- customer / address / contact person ----------------------------
    def test_customer_preconditions(self) -> None:
        for overrides, fragment in (
            ({"contact_name": "Structural Composites Technologies", "company_name": ""}, "named"),
            ({"status": "inactive"}, "not active"),
            ({"contact_type": "vendor"}, "not a customer"),
            ({"currency_code": "USD"}, "not CAD"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(so_tool.SalesOrderError) as caught:
                    so_tool.customer_evidence(fake_contact(**overrides))
                self.assertIn(fragment, str(caught.exception))

    def test_wrong_customer_id_is_refused(self) -> None:
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.customer_evidence(fake_contact(contact_id="96274000000999999"))

    def test_address_must_be_owned_by_the_customer(self) -> None:
        contact = fake_contact()
        contact["billing_address"] = {"address_id": "96274000000999005"}
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.address_evidence([contact["shipping_address"]], contact)
        self.assertIn("not one of the addresses owned", str(caught.exception))

    def test_contact_person_must_be_bon_bacani(self) -> None:
        contact = fake_contact()
        contact["contact_persons"] = [c for c in contact["contact_persons"]
                                      if c["contact_person_id"] != so_tool.CONTACT_PERSON_ID]
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.contact_person_evidence(contact)
        self.assertIn("is not on live customer", str(caught.exception))
        wrong = fake_contact()
        wrong["contact_persons"][1]["first_name"] = "Brian"
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.contact_person_evidence(wrong)
        self.assertIn("not the fixed", str(caught.exception))

    # -- item ------------------------------------------------------------
    def test_item_preconditions(self) -> None:
        for overrides, fragment in (
            ({"name": "FNPT Coupling something else"}, "named"),
            ({"sku": "FNPTCOUPLING-DERAKANE470-3/4\"8\""}, "SKU"),
            ({"status": "inactive"}, "not active"),
            ({"unit": "ea"}, "sold in"),
            ({"actual_available_stock": 1.0}, "physical available stock"),
            ({"actual_available_stock": 0.0}, "physical available stock"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(so_tool.SalesOrderError) as caught:
                    so_tool.item_evidence(fake_item(**overrides))
                self.assertIn(fragment, str(caught.exception))

    def test_committed_stock_does_not_substitute_for_physical_stock(self) -> None:
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.item_evidence(
                fake_item(actual_available_stock=0.0, available_stock=9.0, stock_on_hand=9.0)
            )

    def test_stage_refuses_when_stock_is_short(self) -> None:
        self.item["actual_available_stock"] = 1.0
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.command_stage(mock.Mock())
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    # -- tax -------------------------------------------------------------
    def test_tax_preconditions(self) -> None:
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.tax_evidence([{"tax_id": "1", "tax_name": "ON HST", "tax_percentage": 13.0}])
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.tax_evidence([
                {"tax_id": so_tool.TAX_ID, "tax_name": "ON HST", "tax_percentage": 5.0}
            ])
        self.assertIn("not the fixed 13%", str(caught.exception))
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.tax_evidence([
                {"tax_id": so_tool.TAX_ID, "tax_name": "GST", "tax_percentage": 13.0}
            ])

    def test_the_live_gst_tax_is_never_selected(self) -> None:
        """The GST record stays in the live list and must simply be passed over."""
        evidence = so_tool.tax_evidence(copy.deepcopy(self.taxes))
        self.assertEqual(evidence["tax_id"], so_tool.TAX_ID)
        self.assertEqual(evidence["tax_name"], "ON HST")
        self.assertEqual(Decimal(evidence["tax_percentage"]), Decimal("13"))
        # Removing Ontario HST from the live list refuses; GST is no substitute.
        without_hst = [tax for tax in self.taxes if tax["tax_id"] != so_tool.TAX_ID]
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.tax_evidence(without_hst)
        self.assertIn("is not a live tax", str(caught.exception))

    # -- duplicates ------------------------------------------------------
    def test_existing_reference_is_a_duplicate(self) -> None:
        for reference in ("PO26330", "26330", "po-26330", "  PO 26330 "):
            with self.subTest(reference=reference):
                self.salesorder_rows.append({
                    "salesorder_id": "96274000001500001",
                    "salesorder_number": "SO-00050",
                    "customer_id": "96274000000999999",
                    "status": "open",
                    "reference_number": reference,
                })
                with self.assertRaises(so_tool.SalesOrderError) as caught:
                    so_tool.command_stage(mock.Mock())
                self.assertIn("already appears", str(caught.exception))
                self.salesorder_rows.pop()

    def test_open_same_customer_order_naming_the_po_in_notes_is_a_duplicate(self) -> None:
        self.salesorder_rows.append({
            "salesorder_id": "96274000001500002",
            "salesorder_number": "SO-00051",
            "customer_id": so_tool.CUSTOMER_ID,
            "status": "open",
            "reference_number": "",
        })
        self.salesorder_details["96274000001500002"] = {
            "salesorder_id": "96274000001500002",
            "salesorder_number": "SO-00051",
            "customer_id": so_tool.CUSTOMER_ID,
            "notes": "Against client PO 26330, awaiting stock",
            "terms": "",
            "reference_number": "",
        }
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("already names this PO", str(caught.exception))

    def test_same_total_history_is_not_a_duplicate(self) -> None:
        """SO-00020 shares the money total and must be left alone."""
        path = self.stage()
        plan = self.plan_body(path)
        self.assertEqual(plan["live_evidence"]["duplicates"]["matches"], [])
        # A closed historical order is never even read in detail.
        self.assertNotIn(
            "96274000000508340",
            plan["live_evidence"]["duplicates"]["same_customer_open_orders_inspected"],
        )

    def test_partial_sweep_fails_closed(self) -> None:
        self.page_context = {}
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("cannot be proven complete", str(caught.exception))

    def test_endless_pagination_fails_closed(self) -> None:
        self.page_context = {"has_more_page": True}
        with mock.patch.object(so_tool, "MAX_SALESORDER_PAGES", 3):
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.command_stage(mock.Mock())
        self.assertIn("cannot be proven complete", str(caught.exception))

    # -- the source PDF --------------------------------------------------
    def test_missing_or_altered_pdf_refuses_to_stage(self) -> None:
        self.pdf_path.unlink()
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.command_stage(mock.Mock())
        self.assertIn("missing", str(caught.exception))

    def test_wrong_size_pdf_is_refused(self) -> None:
        self.pdf_path.write_bytes(b"%PDF-1.4 short")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.read_source_pdf()
        self.assertIn("bytes, not the exact", str(caught.exception))

    def test_same_size_different_bytes_is_refused(self) -> None:
        content = bytearray(self.pdf_path.read_bytes())
        content[-1] ^= 0xFF
        self.pdf_path.write_bytes(bytes(content))
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.read_source_pdf()
        self.assertIn("hashes to", str(caught.exception))

    def test_non_pdf_of_the_right_size_and_hash_is_impossible(self) -> None:
        content = b"X" * so_tool.SOURCE_PDF_SIZE
        self.pdf_path.write_bytes(content)
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.read_source_pdf()

    def test_a_symlinked_client_po_is_refused(self) -> None:
        link = self.root / "linked.pdf"
        try:
            link.symlink_to(self.pdf_path)
        except (OSError, NotImplementedError) as exc:  # Windows needs privilege
            self.skipTest(f"symlinks are not creatable here: {exc}")
        with mock.patch.object(so_tool, "SOURCE_PDF_PATH", link):
            with self.assertRaises(so_tool.SalesOrderError) as caught:
                so_tool.read_source_pdf()
        self.assertIn("symlink", str(caught.exception))


class CommandLineTests(unittest.TestCase):
    def test_only_the_two_commissioned_commands_exist(self) -> None:
        parser = so_tool.build_parser()
        actions = [
            action for action in parser._actions
            if getattr(action, "choices", None) and "stage-sct-po26330" in action.choices
        ]
        self.assertEqual(len(actions), 1)
        self.assertEqual(
            sorted(actions[0].choices), ["commit-sct-po26330", "stage-sct-po26330"]
        )

    def test_commit_has_no_default_approval(self) -> None:
        """Approval can only come from Rachad's own typed word."""
        parser = so_tool.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["commit-sct-po26330", "--plan", "x.json"])
        # The time of his message is required too (A3/A5).
        with self.assertRaises(SystemExit):
            parser.parse_args(["commit-sct-po26330", "--plan", "x.json", "--approval", "APPROVED"])
        args = parser.parse_args(
            ["commit-sct-po26330", "--plan", "x.json", "--approval", "APPROVED",
             "--approval-message-utc", "2026-08-11T10:01:00+00:00"]
        )
        self.assertEqual(args.approval, "APPROVED")
        self.assertEqual(args.approval_message_utc, "2026-08-11T10:01:00+00:00")

    def test_stage_takes_no_arguments_so_nothing_can_be_redirected(self) -> None:
        parser = so_tool.build_parser()
        args = parser.parse_args(["stage-sct-po26330"])
        self.assertEqual(args.func, so_tool.command_stage)
        with self.assertRaises(SystemExit):
            parser.parse_args(["stage-sct-po26330", "--customer-id", "1"])


# ---------------------------------------------------------------------------
# Plan integrity
# ---------------------------------------------------------------------------


class PlanIntegrityTests(SalesOrderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan_path = self.stage()

    def test_a_good_plan_loads(self) -> None:
        plan, evidence = so_tool.load_plan(self.plan_path)
        self.assertEqual(plan["action"], so_tool.ACTION)
        self.assertEqual(evidence["post_payload"]["reference_number"], "PO26330")

    def test_edited_plan_fails_the_hash(self) -> None:
        self.rewrite_plan(
            self.plan_path,
            lambda plan: plan["live_evidence"]["post_payload"].__setitem__("date", "2026-08-12"),
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.load_plan(self.plan_path)
        self.assertIn("hash check failed", str(caught.exception))

    def test_resealed_tampering_still_fails_re_derivation(self) -> None:
        """Repairing the hash is not enough: the projection is rebuilt from scratch."""
        for label, mutate in (
            ("line quantity", lambda plan: plan["live_evidence"]["post_payload"].__setitem__(
                "line_items", [{**plan["live_evidence"]["post_payload"]["line_items"][0],
                                "quantity": 4.0}])),
            ("customer", lambda plan: plan["live_evidence"]["post_payload"].__setitem__(
                "customer_id", "96274000000999999")),
            ("rate", lambda plan: plan["live_evidence"]["post_payload"].__setitem__(
                "line_items", [{**plan["live_evidence"]["post_payload"]["line_items"][0],
                                "rate": 45.72}])),
            ("added payload key", lambda plan: plan["live_evidence"]["post_payload"].__setitem__(
                "status", "confirmed")),
            ("totals", lambda plan: plan["live_evidence"]["totals"].__setitem__(
                "total", "999.99")),
            ("item stock", lambda plan: plan["live_evidence"]["item"].__setitem__(
                "actual_available_stock", "99")),
            ("endpoint", lambda plan: plan["live_evidence"].__setitem__(
                "post_endpoint", "POST /books/v3/invoices")),
            ("email disclosure", lambda plan: plan["live_evidence"].__setitem__(
                "email_sent", True)),
            ("risk disclosure", lambda plan: plan["risk"].__setitem__("atomic", True)),
            ("attachment hash", lambda plan: plan["live_evidence"]["attachment"].__setitem__(
                "sha256", "0" * 64)),
            ("duplicate match hidden", lambda plan: plan["live_evidence"]["duplicates"].__setitem__(
                "matches", [{"salesorder_id": "1"}])),
        ):
            with self.subTest(label=label):
                path = self.reseal_plan(self.plan_path, mutate)
                with self.assertRaises(so_tool.SalesOrderError):
                    so_tool.load_plan(path)
                self.plan_path.unlink()
                self.plan_path = self.stage()

    def test_wrong_action_tool_or_version_is_refused(self) -> None:
        for key, value in (
            ("action", "create_draft_invoice"),
            ("tool", "Some Other Tool"),
            ("tool_version", "0.9.0"),
            # Superseded pre-revision PO schemas.
            ("tool_version", "1.0.0"),
            ("schema_version", 1),
            ("schema_version", 2),
            ("schema_version", 3),
            ("approval_required", "OK"),
        ):
            with self.subTest(key=key):
                path = self.reseal_plan(self.plan_path, lambda plan, k=key, v=value: plan.__setitem__(k, v))
                with self.assertRaises(so_tool.SalesOrderError):
                    so_tool.load_plan(path)
                self.plan_path.unlink()
                self.plan_path = self.stage()

    def test_expired_plan_is_refused(self) -> None:
        path = self.reseal_plan(self.plan_path, lambda plan: plan.update({
            "created_utc": "2026-08-01T00:00:00+00:00",
            "expires_utc": "2026-08-02T00:00:00+00:00",
        }))
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.load_plan(path)
        self.assertIn("expired", str(caught.exception))

    def test_stretched_lifetime_is_refused(self) -> None:
        plan = self.plan_body(self.plan_path)
        created = plan["created_utc"]
        path = self.reseal_plan(
            self.plan_path,
            lambda body: body.__setitem__("expires_utc", created.replace("T", "T", 1)[:-6] + "+00:00")
            if False else body.__setitem__("expires_utc", "2026-12-31T00:00:00+00:00"),
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.load_plan(path)
        self.assertIn("24-hour", str(caught.exception))

    def test_foreign_origin_is_refused(self) -> None:
        path = self.reseal_plan(
            self.plan_path,
            lambda plan: plan["origin"].__setitem__("repo_root", r"C:\AgentTeam"),
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            so_tool.load_plan(path)
        self.assertIn("different tool file", str(caught.exception))

    def test_plan_outside_the_plan_folder_is_refused(self) -> None:
        outside = self.root / "elsewhere.json"
        outside.write_text(self.plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        for candidate in (
            str(outside),
            str(self.plan_dir / ".." / "elsewhere.json"),
            "relative.json",
            str(self.plan_dir / "missing.json"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(so_tool.SalesOrderError):
                    so_tool.contained_plan(candidate)

    def test_the_real_superseded_gst5_plan_can_never_commit(self) -> None:
        """The exact plan file staged under the GST-5% build, byte for byte.

        Rachad never approved it and it was never committed. It is deliberately
        left on disk as a record, so this test proves that even a full commit
        with his real approval word refuses it, writes nothing and locks nothing.
        """
        if not SUPERSEDED_PLAN.is_file():  # pragma: no cover - it is kept on disk
            self.skipTest(f"the superseded plan file is not present: {SUPERSEDED_PLAN}")
        original = SUPERSEDED_PLAN.read_bytes()
        body = json.loads(original.decode("utf-8"))
        # It really is the GST-5% plan, and it really is intact.
        self.assertEqual(body["sha256"], so_tool.SUPERSEDED_PLAN_SHA256)
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["tool_version"], "1.0.0")
        self.assertEqual(body["live_evidence"]["tax"]["tax_id"], SUPERSEDED_GST_TAX_ID)
        self.assertEqual(body["live_evidence"]["totals"]["tax_total"], "5.02")
        self.assertEqual(body["live_evidence"]["totals"]["total"], "105.42")

        copied = self.plan_dir / SUPERSEDED_PLAN.name
        copied.write_bytes(original)
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(copied)
        message = str(caught.exception)
        self.assertIn("SUPERSEDED", message)
        self.assertIn("Ontario HST 13%", message)
        self.assertIn("never committed", message)
        # Not a write, not a lock, and the original file is untouched.
        self.assert_no_write_attempted()
        self.assertEqual(SUPERSEDED_PLAN.read_bytes(), original)

    def test_an_old_schema_or_version_plan_cannot_commit(self) -> None:
        """The version and schema bump alone refuse the superseded build's plans.

        This does not depend on the one known plan file: any plan carrying the
        old schema or the old tool version is refused, with its hash repaired,
        before approval reaches a lock or a network call.
        """
        for key, value in (("schema_version", 1), ("tool_version", "1.0.0")):
            with self.subTest(key=key):
                path = self.reseal_plan(
                    self.plan_path, lambda plan, k=key, v=value: plan.__setitem__(k, v)
                )
                with self.assertRaises(so_tool.SalesOrderError) as caught:
                    self.commit(path)
                self.assertIn("schema version", str(caught.exception))
                self.assert_no_write_attempted()
                self.plan_path.unlink()
                self.plan_path = self.stage()

    def test_forged_gst5_figures_under_the_new_version_still_fail(self) -> None:
        """Forging the version forward does not smuggle the old tax through.

        The whole projection is rebuilt from the plan's own stored evidence, so
        a GST-5% tax record or a CAD 105.42 total is refused on its own merits.
        """
        for label, mutate in (
            ("tax record", lambda plan: plan["live_evidence"]["tax"].update({
                "tax_id": SUPERSEDED_GST_TAX_ID, "tax_name": "GST", "tax_percentage": "5",
            })),
            ("tax total", lambda plan: plan["live_evidence"]["totals"].update({
                "tax_percentage": "5", "tax_total": "5.02", "total": "105.42",
            })),
            ("line tax", lambda plan: plan["live_evidence"]["post_payload"]["line_items"][0]
                .__setitem__("tax_id", SUPERSEDED_GST_TAX_ID)),
        ):
            with self.subTest(label=label):
                path = self.reseal_plan(self.plan_path, mutate)
                with self.assertRaises(so_tool.SalesOrderError):
                    self.commit(path)
                self.assert_no_write_attempted()
                self.plan_path.unlink()
                self.plan_path = self.stage()

    def test_non_json_plan_is_refused(self) -> None:
        other = self.plan_dir / "plan.txt"
        other.write_text("{}", encoding="utf-8")
        with self.assertRaises(so_tool.SalesOrderError):
            so_tool.contained_plan(str(other))


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


class CommitRefusalTests(SalesOrderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan_path = self.stage()

    def test_wrong_approval_refuses_before_lock_and_before_network(self) -> None:
        for approval in ("approved but wait", "hold on", "APPROVED?", "", "not yet"):
            with self.subTest(approval=approval):
                with self.assertRaises(so_tool.SalesOrderError) as caught:
                    self.commit(self.plan_path, approval)
                self.assertIn("unambiguous go" if approval else "no approval text", str(caught.exception))
                self.assert_no_write_attempted()

    def test_missing_create_scope_refuses_before_lock_and_before_network(self) -> None:
        self.vault["scopes"] = list(so_tool.REQUIRED_READ_SCOPES)
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        message = str(caught.exception)
        self.assertIn("ZohoBooks.salesorders.CREATE", message)
        self.assertIn("Nothing was locked", message)
        self.assert_no_write_attempted()

    def test_forbidden_scope_refuses_before_any_write(self) -> None:
        self.vault["scopes"].append("ZohoBooks.salesorders.UPDATE")
        with self.assertRaises(so_tool.SalesOrderError):
            self.commit(self.plan_path)
        self.assert_no_write_attempted()

    def test_altered_pdf_refuses_before_lock(self) -> None:
        content = bytearray(self.pdf_path.read_bytes())
        content[10] ^= 0xFF
        self.pdf_path.write_bytes(bytes(content))
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("hashes to", str(caught.exception))
        self.assert_no_write_attempted()

    def test_fingerprint_drift_refuses_before_lock_and_leaves_the_plan_usable(self) -> None:
        for label, build_drift in (
            ("customer", lambda s: s.contact.__setitem__("status", "inactive")),
            ("item", lambda s: s.item.__setitem__("actual_available_stock", 1.0)),
            ("tax", lambda s: s.taxes[0].__setitem__("tax_percentage", 5.0)),
            ("contact person", lambda s: s.contact["contact_persons"].pop()),
            ("address", lambda s: s.contact["billing_address"].__setitem__("city", "Brandon")),
        ):
            with self.subTest(label=label):
                self.reset()  # setUp stages the plan; do not stage a second one
                build_drift(self)
                with self.assertRaises(so_tool.SalesOrderError):
                    self.commit(self.plan_path)
                # A drift is a FREE refusal: no write, and no lock burned, so the
                # approved plan survives for a later commit.
                self.assert_no_write_attempted()

    def test_a_duplicate_appearing_after_approval_refuses_before_lock(self) -> None:
        self.salesorder_rows.append({
            "salesorder_id": "96274000001500003",
            "salesorder_number": "SO-00052",
            "customer_id": so_tool.CUSTOMER_ID,
            "status": "open",
            "reference_number": "PO26330",
        })
        with self.assertRaises(so_tool.SalesOrderError):
            self.commit(self.plan_path)
        self.assert_no_write_attempted()

    def test_a_changed_contract_refuses_before_lock(self) -> None:
        self.probe.pop("shipment_date")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("contract changed after review", str(caught.exception))
        self.assert_no_write_attempted()

    def test_organization_mismatch_refuses_before_lock(self) -> None:
        self.vault["books_organization_id"] = "999999"
        with self.assertRaises(so_tool.SalesOrderError):
            self.commit(self.plan_path)
        self.assert_no_write_attempted()


class CommitSuccessTests(SalesOrderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan_path = self.stage()

    def test_commit_creates_the_order_attaches_the_po_and_verifies_both(self) -> None:
        self.allow_writes()
        with mock.patch("builtins.print") as printed:
            self.commit(self.plan_path)
        report = json.loads(
            "".join(call.args[0] for call in printed.call_args_list if call.args)
        )
        self.assertEqual(report["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(report["salesorder_id"], NEW_SALESORDER_ID)
        self.assertEqual(report["salesorder_number"], AUTO_NUMBER)
        self.assertEqual(report["salesorder_status_verified"], "draft")
        self.assertEqual(report["reference_number"], "PO26330")
        self.assertEqual(report["totals_verified"]["total"], "113.45")
        self.assertEqual(report["totals_verified"]["tax_total"], "13.05")
        self.assertEqual(report["attachment"]["verified_sha256"], so_tool.SOURCE_PDF_SHA256)
        self.assertTrue(report["attachment"]["matches_original"])
        self.assertTrue(report["attachment"]["documents_checked"])
        self.assertFalse(report["email_sent"])
        self.assertFalse(report["atomic"])
        self.assertTrue(report["replay_locked"])

    def test_exactly_one_create_one_attach_and_one_download(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        methods = [(call["method"], call["route"]) for call in self.write_calls]
        self.assertEqual(methods, [
            ("POST", "/books/v3/salesorders"),
            ("POST", f"/books/v3/salesorders/{NEW_SALESORDER_ID}/attachment"),
            ("GET", f"/books/v3/salesorders/{NEW_SALESORDER_ID}/attachment"),
        ])
        self.assertNotIn("PUT", [call["method"] for call in self.write_calls])
        self.assertNotIn("DELETE", [call["method"] for call in self.write_calls])

    def test_the_lock_exists_before_the_first_write(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        self.assertEqual(len(self.write_calls[0]["locks_before"]), 1)

    def test_the_posted_payload_is_exactly_the_approved_one(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        payload = self.write_calls[0]["payload"]
        self.assertEqual(payload, self.plan_body(self.plan_path)["live_evidence"]["post_payload"])
        self.assertNotIn("salesorder_number", payload)
        self.assertNotIn("status", payload)
        self.assertNotIn("ignore_auto_number_generation", payload)
        self.assertEqual(payload["payment_terms"], 30)
        self.assertEqual(payload["payment_terms_label"], "Net 30")
        self.assertEqual(payload["line_items"][0]["rate"], 50.2)
        self.assertEqual(payload["line_items"][0]["quantity"], 2.0)

    def test_the_uploaded_body_is_the_original_pdf(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        upload = self.write_calls[1]
        self.assertIn("multipart/form-data; boundary=", upload["content_type"])
        self.assertIn(self.pdf_path.read_bytes(), upload["body"])
        self.assertNotIn("can_send_in_mail", upload["url"])

    def test_the_query_string_is_only_the_organization(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        for call in self.write_calls:
            query = call["url"].split("?", 1)[1]
            self.assertEqual(query, f"organization_id={ORGANIZATION['organization_id']}")

    def test_a_committed_plan_cannot_be_replayed(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        before = len(self.write_calls)
        # The fresh duplicate sweep now sees the order this plan created, so the
        # replay is refused before the lock is even reached. Either way, nothing
        # is written twice.
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("already appears", str(caught.exception))
        self.assertEqual(len(self.write_calls), before)

    def test_the_replay_lock_alone_stops_a_second_commit(self) -> None:
        """Even with the live state unchanged, a consumed plan is spent."""
        plan = self.plan_body(self.plan_path)
        so_tool.write_lock(
            so_tool.lock_path(plan["sha256"]),
            {"plan_sha256": plan["sha256"], "status": "committed_verified"},
            exclusive=True,
        )
        self.allow_writes()
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("cannot be replayed", str(caught.exception))
        self.assertEqual(self.write_calls, [])

    def test_the_receipt_records_the_verified_outcome(self) -> None:
        self.allow_writes()
        self.commit(self.plan_path)
        action, detail = self.append_receipt.call_args[0]
        self.assertIn("committed_verified", action)
        self.assertIn(NEW_SALESORDER_ID, detail)
        self.assertIn("status=draft", detail)
        self.assertIn(so_tool.SOURCE_PDF_SHA256, detail)
        self.assertIn("email_sent=false", detail)

    def test_commit_without_a_proven_shipment_date_field(self) -> None:
        """The required date then lives only in the notes, and is verified there."""
        self.reset()
        self.plan_path.unlink()  # re-stage against a contract without the field
        self.probe.pop("shipment_date")
        self.plan_path = self.stage()
        plan = self.plan_body(self.plan_path)
        self.assertNotIn("shipment_date", plan["live_evidence"]["post_payload"])
        self.assertIn("the fixed notes ONLY", plan["payload"]["required_date_placement"])
        self.allow_writes()
        self.commit(self.plan_path)
        payload = self.write_calls[0]["payload"]
        self.assertNotIn("shipment_date", payload)
        self.assertIn("Required: 2026-08-12", payload["notes"])


class CommitFailureTests(SalesOrderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan_path = self.stage()

    def lock_body(self) -> dict:
        locks = sorted((self.plan_dir / ".commit-locks").glob("*.json"))
        self.assertEqual(len(locks), 1)
        return json.loads(locks[0].read_text(encoding="utf-8"))

    def test_a_non_draft_result_is_indeterminate_and_locked(self) -> None:
        original = self.created_salesorder

        def build(payload, **kw):
            record = original(payload, **kw)
            record["status"] = "open"
            self.salesorder_details[NEW_SALESORDER_ID] = record
            return record

        self.created_salesorder = build
        self.allow_writes(register=False)
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        message = str(caught.exception)
        self.assertIn("indeterminate", message)
        self.assertIn(NEW_SALESORDER_ID, message)
        self.assertIn("NOTHING was cleaned up", message)
        self.assertIn("re-stage", message.casefold())
        self.assertNotIn("permanent", message.casefold())
        lock = self.lock_body()
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])
        self.assertTrue(lock["create_attempted"])
        self.assertFalse(lock["attachment_attempted"])
        self.assertEqual(lock["salesorder_id"], NEW_SALESORDER_ID)
        # No cleanup call of any kind followed the failure.
        self.assertEqual(len(self.write_calls), 1)

    def test_an_attachment_failure_after_create_is_reported_and_needs_restage(self) -> None:
        self.allow_writes(
            on_attach=lambda record: (_ for _ in ()).throw(
                HTTPError(record["url"], 400, "Bad Request", None, None)
            )
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        message = str(caught.exception)
        self.assertIn(NEW_SALESORDER_ID, message)
        self.assertIn("attachment POST was ALSO issued", message)
        self.assertIn("nothing is retried silently", message)
        self.assertIn("his go to the NEW plan", message)
        lock = self.lock_body()
        self.assertTrue(lock["attachment_attempted"])
        self.assertEqual(lock["stage_of_failure"], "after_create_and_attachment_attempt")
        self.assertFalse(lock["permanent_lock"])
        self.assertEqual(len(self.write_calls), 2)
        # A second attempt is refused by the lock, not re-tried.
        with self.assertRaises(so_tool.SalesOrderError):
            self.commit(self.plan_path)
        self.assertEqual(len(self.write_calls), 2)

    def test_attachment_content_drift_is_indeterminate(self) -> None:
        self.allow_writes(attachment_bytes=b"%PDF-1.4 a different document")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("not the original client PO", str(caught.exception))
        self.assertTrue(self.lock_body()["attachment_attempted"])

    def test_a_json_message_instead_of_the_file_is_not_a_verification(self) -> None:
        self.allow_writes(attachment_bytes=b'{"code":0,"message":"ok"}')
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("JSON message rather than the stored file", str(caught.exception))

    def test_an_empty_download_is_not_a_verification(self) -> None:
        self.allow_writes(attachment_bytes=b"")
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("cannot be confirmed", str(caught.exception))

    def test_a_timeout_on_create_is_indeterminate_with_no_id(self) -> None:
        self.allow_writes(
            on_create=lambda record: (_ for _ in ()).throw(URLError("timed out"))
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        message = str(caught.exception)
        self.assertIn("UNKNOWN", message)
        self.assertIn("NO attachment was uploaded", message)
        lock = self.lock_body()
        self.assertEqual(lock["salesorder_id"], "")
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])

    def test_a_create_response_without_an_id_is_indeterminate(self) -> None:
        self.allow_writes(on_create=lambda record: {"code": 0, "message": "ok"})
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("UNKNOWN", str(caught.exception))

    def test_a_nonzero_zoho_code_is_a_failure(self) -> None:
        self.allow_writes(
            on_create=lambda record: {"code": 15, "message": "Please ensure ..."}
        )
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("invalid or unknown result", str(caught.exception))

    def test_wrong_line_values_on_read_back_are_caught(self) -> None:
        cases = {
            "quantity": lambda so: so["line_items"][0].__setitem__("quantity", 4.0),
            "rate": lambda so: so["line_items"][0].__setitem__("rate", 45.72),
            "item": lambda so: so["line_items"][0].__setitem__(
                "item_id", "96274000000508303"),
            "tax": lambda so: so["line_items"][0].__setitem__("tax_id", "96274000000035514"),
            "description": lambda so: so["line_items"][0].__setitem__("description", "x"),
            "second line": lambda so: so["line_items"].append(
                copy.deepcopy(so["line_items"][0])),
            "discount": lambda so: so["line_items"][0].__setitem__("discount", 5.0),
            "totals": lambda so: so.__setitem__("total", 999.99),
            "shipping": lambda so: so.__setitem__("shipping_charge", 25.0),
            "adjustment": lambda so: so.__setitem__("adjustment", -1.0),
            "emailed": lambda so: so.__setitem__("is_emailed", True),
            "reference": lambda so: so.__setitem__("reference_number", "PO26331"),
            "notes": lambda so: so.__setitem__("notes", "Tag only"),
            "payment terms": lambda so: so.__setitem__("payment_terms", -3),
            "customer": lambda so: so.__setitem__("customer_id", "96274000000999999"),
            "billing address": lambda so: so.__setitem__(
                "billing_address", {"address_id": "96274000000999005"}),
            "shipment date": lambda so: so.__setitem__("shipment_date", "2026-09-01"),
            "contact person": lambda so: so.__setitem__("contact_persons", []),
            "exchange rate": lambda so: so.__setitem__("exchange_rate", 1.37),
            "po as number": lambda so: so.__setitem__("salesorder_number", "PO26330"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.reset()  # setUp stages the plan; do not stage a second one
                original = self.created_salesorder

                def build(payload, _mutate=mutate, _original=original, **kw):
                    record = _original(payload, **kw)
                    _mutate(record)
                    self.salesorder_details[NEW_SALESORDER_ID] = record
                    self.salesorder_rows.append({
                        "salesorder_id": NEW_SALESORDER_ID,
                        "salesorder_number": record.get("salesorder_number"),
                        "customer_id": record.get("customer_id"),
                        "status": record.get("status"),
                        "reference_number": record.get("reference_number"),
                    })
                    return record

                self.created_salesorder = build
                self.allow_writes(register=False)
                with self.assertRaises(so_tool.SalesOrderError):
                    self.commit(self.plan_path)
                self.assertTrue(self.lock_body()["create_attempted"])

    def test_a_second_copy_after_the_write_is_caught(self) -> None:
        self.allow_writes()
        original = self.created_salesorder

        def build(payload, **kw):
            record = original(payload, **kw)
            self.salesorder_rows.append({
                "salesorder_id": "96274000001900099",
                "salesorder_number": "SO-00043",
                "customer_id": so_tool.CUSTOMER_ID,
                "status": "draft",
                "reference_number": "PO26330",
            })
            return record

        self.created_salesorder = build
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("not exactly the one just created", str(caught.exception))

    def test_wrong_attachment_metadata_is_caught(self) -> None:
        self.allow_writes()
        original = self.created_salesorder

        def build(payload, **kw):
            record = original(payload, **kw)
            record["documents"] = [{"document_id": "1", "file_name": "something-else.pdf"}]
            self.salesorder_details[NEW_SALESORDER_ID] = record
            return record

        self.created_salesorder = build
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("not the approved", str(caught.exception))

    def test_two_attached_documents_are_caught(self) -> None:
        self.allow_writes()
        original = self.created_salesorder

        def build(payload, **kw):
            record = original(payload, **kw)
            record["documents"] = [
                {"file_name": so_tool.SOURCE_PDF_NAME},
                {"file_name": so_tool.SOURCE_PDF_NAME},
            ]
            self.salesorder_details[NEW_SALESORDER_ID] = record
            return record

        self.created_salesorder = build
        with self.assertRaises(so_tool.SalesOrderError) as caught:
            self.commit(self.plan_path)
        self.assertIn("not the single client PO", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
