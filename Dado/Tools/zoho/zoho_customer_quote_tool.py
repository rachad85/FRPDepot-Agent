#!/usr/bin/env python
"""FRP Depot Zoho Customer & Quote Draft Tool.

Commissioned by Rachad Homsi on 2026-07-23.

Allowed service writes:
- POST /books/v3/contacts (create customer only)
- POST /books/v3/estimates (create draft estimate only)

Forbidden: sending/emailing, marking sent/accepted/declined, updates, deletes,
Inventory writes, and any unapproved numeric value.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Customer & Quote Draft Tool"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_plans"
ALLOWED_POSTS = {
    "customer": "/books/v3/contacts",
    "quote": "/books/v3/estimates",
}
CUSTOMER_FIELDS = {
    "contact_name", "company_name", "contact_type", "customer_sub_type",
    "website", "billing_address", "shipping_address", "contact_persons",
    "currency_id", "notes", "language_code",
}
ADDRESS_FIELDS = {
    "attention", "address", "street2", "city", "state", "state_code",
    "zip", "country", "phone", "fax",
}
CONTACT_PERSON_FIELDS = {
    "salutation", "first_name", "last_name", "email", "phone", "mobile",
    "designation", "department", "is_primary_contact",
}
QUOTE_FIELDS = {
    "customer_id", "date", "expiry_date", "reference_number", "line_items",
    "notes", "terms", "shipping_charge", "adjustment", "adjustment_description",
    "discount", "is_discount_before_tax", "discount_type", "template_id",
    "salesperson_id", "currency_id", "exchange_rate", "location_id",
}
LINE_ITEM_FIELDS = {
    "item_id", "name", "description", "quantity", "rate", "unit", "discount",
    "tax_id", "location_id",
}
SOURCE_FIELDS = {
    "quantity_source", "rate_source", "discount_source", "tax_source",
}
TOP_SOURCE_FIELDS = {
    "shipping_charge_source", "adjustment_source", "discount_source",
    "exchange_rate_source",
}


class DraftToolError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def plan_hash(plan_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(plan_without_hash).encode("utf-8")).hexdigest()


def read_json(path: str) -> dict[str, Any]:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftToolError(f"Input JSON is unreadable: {path}") from exc
    if not isinstance(result, dict):
        raise DraftToolError("Input JSON must contain one object.")
    return result


def reject_extra(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise DraftToolError(f"Unsupported {label} field(s): {', '.join(extra)}")


def clean_address(value: Any, label: str) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise DraftToolError(f"{label} must be an object.")
    reject_extra(value, ADDRESS_FIELDS, label)
    return {key: item for key, item in value.items() if item not in (None, "")}


def clean_contact_person(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DraftToolError(f"contact_persons[{index}] must be an object.")
    reject_extra(value, CONTACT_PERSON_FIELDS, f"contact_persons[{index}]")
    clean = {key: item for key, item in value.items() if item not in (None, "")}
    if not clean.get("email") and not clean.get("first_name") and not clean.get("last_name"):
        raise DraftToolError(f"contact_persons[{index}] needs a name or email.")
    return clean


def stage_plan(kind: str, payload: dict[str, Any], sources: dict[str, Any], summary: dict[str, Any]) -> Path:
    core = {
        "tool": TOOL_NAME,
        "kind": kind,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "sources": sources,
        "summary": summary,
    }
    digest = plan_hash(core)
    plan = dict(core)
    plan["sha256"] = digest
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{kind}_{digest[:8]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(f"zoho_{kind}_plan_staged", str(path))
    return path


def command_stage_customer(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    reject_extra(raw, CUSTOMER_FIELDS, "customer")
    name = str(raw.get("contact_name") or "").strip()
    if not name:
        raise DraftToolError("contact_name is required.")
    contact_type = str(raw.get("contact_type") or "customer").casefold()
    if contact_type != "customer":
        raise DraftToolError("This tool creates customers only, never vendors.")
    payload = {key: value for key, value in raw.items() if value not in (None, "", [], {})}
    payload["contact_name"] = name
    payload["contact_type"] = "customer"
    if "billing_address" in raw:
        payload["billing_address"] = clean_address(raw.get("billing_address"), "billing_address")
    if "shipping_address" in raw:
        payload["shipping_address"] = clean_address(raw.get("shipping_address"), "shipping_address")
    if "contact_persons" in raw:
        if not isinstance(raw["contact_persons"], list):
            raise DraftToolError("contact_persons must be a list.")
        payload["contact_persons"] = [
            clean_contact_person(value, index) for index, value in enumerate(raw["contact_persons"])
        ]
    summary = {
        "contact_name": name,
        "company_name": payload.get("company_name"),
        "primary_email": next(
            (person.get("email") for person in payload.get("contact_persons", []) if person.get("email")),
            None,
        ),
    }
    path = stage_plan("customer", payload, {"record_source": args.source}, summary)
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    print(json.dumps({"plan": str(path), "summary": summary, "approval": f"APPROVE CUSTOMER {digest[:8]}"}, indent=2))


def numeric(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DraftToolError(f"{label} must be a number.")
    return float(value)


def nonempty_source(value: Any, label: str) -> str:
    source = str(value or "").strip()
    if not source:
        raise DraftToolError(f"{label} is required. Every quote number needs a source.")
    return source


def command_stage_quote(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    reject_extra(raw, QUOTE_FIELDS | TOP_SOURCE_FIELDS, "quote")
    customer_id = str(raw.get("customer_id") or "").strip()
    if not customer_id.isdigit():
        raise DraftToolError("customer_id must be the numeric Zoho customer ID.")
    raw_lines = raw.get("line_items")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise DraftToolError("At least one line item is required.")
    payload: dict[str, Any] = {
        key: value for key, value in raw.items()
        if key in QUOTE_FIELDS and key != "line_items" and value not in (None, "")
    }
    payload["customer_id"] = customer_id
    payload["status"] = "draft"
    sources: dict[str, Any] = {"line_items": []}
    clean_lines = []
    for index, raw_line in enumerate(raw_lines):
        if not isinstance(raw_line, dict):
            raise DraftToolError(f"line_items[{index}] must be an object.")
        reject_extra(raw_line, LINE_ITEM_FIELDS | SOURCE_FIELDS, f"line_items[{index}]")
        item_id = str(raw_line.get("item_id") or "").strip()
        if not item_id.isdigit():
            raise DraftToolError(f"line_items[{index}].item_id must be a numeric Zoho item ID.")
        quantity = numeric(raw_line.get("quantity"), f"line_items[{index}].quantity")
        rate = numeric(raw_line.get("rate"), f"line_items[{index}].rate")
        if quantity <= 0 or rate < 0:
            raise DraftToolError(f"line_items[{index}] has an invalid quantity or rate.")
        line_sources = {
            "quantity": nonempty_source(raw_line.get("quantity_source"), f"line_items[{index}].quantity_source"),
            "rate": nonempty_source(raw_line.get("rate_source"), f"line_items[{index}].rate_source"),
        }
        if numeric(raw_line.get("discount", 0), f"line_items[{index}].discount") != 0:
            line_sources["discount"] = nonempty_source(
                raw_line.get("discount_source"), f"line_items[{index}].discount_source"
            )
        if raw_line.get("tax_id"):
            line_sources["tax"] = nonempty_source(
                raw_line.get("tax_source"), f"line_items[{index}].tax_source"
            )
        clean_line = {
            key: value for key, value in raw_line.items()
            if key in LINE_ITEM_FIELDS and value not in (None, "")
        }
        clean_line["item_id"] = item_id
        clean_line["quantity"] = quantity
        clean_line["rate"] = rate
        clean_lines.append(clean_line)
        sources["line_items"].append(line_sources)
    payload["line_items"] = clean_lines
    for field in ("shipping_charge", "adjustment", "discount", "exchange_rate"):
        if field in payload:
            value = numeric(payload[field], field)
            payload[field] = value
            if value != 0:
                source_field = f"{field}_source"
                sources[field] = nonempty_source(raw.get(source_field), source_field)
    summary = {
        "customer_id": customer_id,
        "reference_number": payload.get("reference_number"),
        "line_items": [
            {
                "item_id": line["item_id"],
                "quantity": line["quantity"],
                "rate": line["rate"],
                "sources": sources["line_items"][index],
            }
            for index, line in enumerate(clean_lines)
        ],
        "shipping_charge": payload.get("shipping_charge", 0),
        "adjustment": payload.get("adjustment", 0),
        "discount": payload.get("discount", 0),
    }
    path = stage_plan("quote", payload, sources, summary)
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    print(json.dumps({"plan": str(path), "summary": summary, "approval": f"APPROVE QUOTE {digest[:8]}"}, indent=2))


def load_verified_plan(path: str, expected_kind: str) -> dict[str, Any]:
    plan = read_json(path)
    saved_hash = str(plan.pop("sha256", ""))
    actual_hash = plan_hash(plan)
    if not saved_hash or saved_hash != actual_hash:
        raise DraftToolError("Plan hash check failed. The plan may have changed after review.")
    if plan.get("tool") != TOOL_NAME or plan.get("kind") != expected_kind:
        raise DraftToolError("The plan belongs to a different tool or action.")
    plan["sha256"] = saved_hash
    return plan


def api_post_allowed(
    access_token: str,
    api_domain: str,
    kind: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = ALLOWED_POSTS.get(kind)
    if path not in ALLOWED_POSTS.values():
        raise DraftToolError("REFUSED: service POST endpoint is not allowlisted.")
    request = Request(
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DraftToolError(f"Zoho {kind} creation failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise DraftToolError(f"Zoho API could not be reached: {exc.reason}") from exc
    if result.get("code") not in (None, 0):
        raise DraftToolError(f"Zoho {kind} creation failed: {result.get('message') or result.get('code')}")
    return result


def exact_customer_exists(access_token: str, vault: dict[str, Any], contact_name: str) -> dict[str, Any] | None:
    query = urlencode(
        {
            "organization_id": vault["books_organization_id"],
            "contact_name_contains": contact_name,
            "page": 1,
            "per_page": 200,
        }
    )
    result = zoho_tool.api_get(access_token, str(vault["api_domain"]), f"/books/v3/contacts?{query}")
    for contact in result.get("contacts") or []:
        if str(contact.get("contact_name") or "").strip().casefold() == contact_name.strip().casefold():
            return contact
    return None


def command_commit(args: argparse.Namespace, kind: str) -> None:
    plan = load_verified_plan(args.plan, kind)
    digest = plan["sha256"]
    expected = f"APPROVE {'CUSTOMER' if kind == 'customer' else 'QUOTE'} {digest[:8]}"
    if args.approval != expected:
        raise DraftToolError(f"Exact Rachad approval required: {expected}")
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    required_scope = "ZohoBooks.contacts.CREATE" if kind == "customer" else "ZohoBooks.estimates.CREATE"
    if required_scope not in (vault.get("scopes") or []):
        raise DraftToolError(f"Saved Zoho connection lacks {required_scope}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    if kind == "customer":
        name = str(plan["payload"]["contact_name"])
        existing = exact_customer_exists(access_token, vault, name)
        if existing:
            raise DraftToolError(
                f"Customer already exists with Zoho ID {existing.get('contact_id')}; no duplicate was created."
            )
    result = api_post_allowed(
        access_token,
        str(vault["api_domain"]),
        kind,
        str(vault["books_organization_id"]),
        plan["payload"],
    )
    zoho_tool.save_vault(vault)
    if kind == "customer":
        record = result.get("contact") or {}
        record_id = str(record.get("contact_id") or "")
        if not record_id:
            raise DraftToolError("Zoho returned success without a customer ID.")
        zoho_tool.append_receipt(
            "zoho_customer_created_by_named_tool",
            f"contact_id={record_id}; plan={args.plan}; sha256={digest}",
        )
        print(json.dumps({"created": "customer", "contact_id": record_id, "contact_name": record.get("contact_name")}, indent=2))
        return
    record = result.get("estimate") or {}
    record_id = str(record.get("estimate_id") or "")
    status = str(record.get("status") or "").casefold()
    zoho_tool.append_receipt(
        "zoho_draft_estimate_created_by_named_tool",
        f"estimate_id={record_id}; status={status}; plan={args.plan}; sha256={digest}",
    )
    if not record_id:
        raise DraftToolError("Zoho returned success without an estimate ID.")
    if status != "draft":
        raise DraftToolError(
            f"Zoho created estimate {record_id} with unexpected status {status or 'unknown'}. No further action taken."
        )
    print(
        json.dumps(
            {
                "created": "draft_estimate",
                "estimate_id": record_id,
                "estimate_number": record.get("estimate_number"),
                "status": status,
                "sent": False,
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_customer = commands.add_parser("stage-customer")
    stage_customer.add_argument("--input", required=True)
    stage_customer.add_argument("--source", required=True)
    stage_customer.set_defaults(func=command_stage_customer)
    stage_quote = commands.add_parser("stage-quote")
    stage_quote.add_argument("--input", required=True)
    stage_quote.set_defaults(func=command_stage_quote)
    commit_customer = commands.add_parser("commit-customer")
    commit_customer.add_argument("--plan", required=True)
    commit_customer.add_argument("--approval", required=True)
    commit_customer.set_defaults(func=lambda args: command_commit(args, "customer"))
    commit_quote = commands.add_parser("commit-quote")
    commit_quote.add_argument("--plan", required=True)
    commit_quote.add_argument("--approval", required=True)
    commit_quote.set_defaults(func=lambda args: command_commit(args, "quote"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (DraftToolError, zoho_tool.ZohoError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
