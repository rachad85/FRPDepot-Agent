#!/usr/bin/env python
"""FRP Depot Zoho J26-403 Fixed Revision Tool.

Commissioned by Rachad Homsi on 2026-08-21. He asked to add six custom dip
tubes and twenty-four lifting lugs / anchor clips to the ALREADY-EMAILED D441
air purchase order sent to Fei, and the same two items to the Troy Dualam quote
at the supplier USD unit cost multiplied by 3.6. No commissioned tool could do
that: `zoho_purchase_order_tool.py` only CREATES a Draft purchase order, and the
general estimate revision in `zoho_customer_quote_tool.py` refuses an `accepted`
estimate and refuses free-text lines outright. Told so, he answered "Proceed" to
building and testing ONE fixed staged revision tool limited to PO-00010 and
QT-000034. That authorizes BUILD AND TESTS ONLY. It is not approval of any plan
and not authority for any Zoho write.

THE WHOLE REACHABLE SURFACE IS TWO INDEPENDENT FIXED ACTIONS:
    purchase_order_revision  -> one PUT /books/v3/purchaseorders/96274000001598034
    estimate_revision        -> one PUT /books/v3/estimates/96274000001602028
Each has its OWN plan, its OWN approval and its OWN permanent replay lock. One
plan can never carry both records, and one APPROVED can never answer two plans.
There is no POST, PATCH or DELETE verb anywhere in this module, no create, clone,
void, cancel, approve, accept, decline, reopen, convert, invoice, receive, bill,
pay, attach, status or template route, no browser path, no Inventory write, no
item creation and no mail transport of any kind.

WHAT EACH PUT DOES, AND ONLY THIS: it resends every ORIGINAL live line exactly
once in live order, each carrying its own line_item_id and item_id, and then
APPENDS the two fixed non-catalog lines once each. Zoho deletes lines a PUT
omits, which is why the complete list is always resent; nothing here can add a
third line, drop, reorder or substitute an original one, or change an original
quantity, rate, tax or description.  Every header value is the PRESERVED live
value, and no status field is sent at all.

*** THE TWO ADDITIONS ARE NON-STANDARD FREE-TEXT LINES ON PURPOSE. ***
They carry name, description, quantity, rate and unit -- and deliberately NO
item_id, NO line_item_id, NO SKU, no stock field and no Inventory route. Rachad
asked for non-standard items; creating dummy catalog items as a workaround would
be a silent, permanent change to FRP Depot's item list that he did not ask for.

*** THIS BUILD CANNOT COMMIT, AND SAYS SO INSTEAD OF INVENTING A PAYLOAD. ***
Five contract facts this action depends on are NOT PROVEN in this tree (see
CONTRACT_FACTS): the purchase-order PUT route itself, an in-place update of an
emailed open purchase order, an in-place update of an ACCEPTED estimate, and a
free-text line append on either record. Staging is GET-only, so it runs and
discloses every one of them in the plan it writes. COMMIT REFUSES -- before the
replay lock, before the vault, before the token and before any network call --
while any required fact is unproven. Proving them needs a live capture or
Zoho's own published contract, which this job is explicitly not authorized to do.

*** THE LIFTING-LUG SUPPLIER COST IS NOT KNOWN AND IS NEVER GUESSED. ***
No live Fei quotation carrying that price existed at the last read. The only
caller-supplied value in this whole tool is that one USD unit cost, and it is
admitted only with a concrete source string AND a source artifact whose SHA-256
matches byte-for-byte. Without it NEITHER plan stages -- not even the purchase
order one -- because both records must move together or the quote misprices the
job.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho J26-403 Fixed Revision Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_j26_403_revision_plans"
LOCK_DIRNAME = ".commit-locks"
PLAN_LIFETIME_HOURS = 24
# Rachad's ruling: his approval is ONE PLAIN WORD, never a checksum, and it must
# come FROM HIS OWN MESSAGE answering the staged plan. Dado relays it and never
# supplies, types first or infers it. Commissioning and the word "Proceed" are
# not approval.
APPROVAL_WORD = "APPROVED"

ACTION_PURCHASE_ORDER = "purchase_order_revision"
ACTION_ESTIMATE = "estimate_revision"
PLAN_KINDS = {
    ACTION_PURCHASE_ORDER: "j26_403_purchase_order_revision",
    ACTION_ESTIMATE: "j26_403_estimate_revision",
}

PURCHASE_ORDER_UPDATE_SCOPE = "ZohoBooks.purchaseorders.UPDATE"
ESTIMATE_UPDATE_SCOPE = "ZohoBooks.estimates.UPDATE"
ACTION_UPDATE_SCOPES = {
    ACTION_PURCHASE_ORDER: PURCHASE_ORDER_UPDATE_SCOPE,
    ACTION_ESTIMATE: ESTIMATE_UPDATE_SCOPE,
}
# Every scope that would widen this beyond the two commissioned in-place
# revisions. The tool refuses to run at all while the saved connection holds one.
FORBIDDEN_SCOPES = (
    "ZohoBooks.purchaseorders.DELETE",
    "ZohoBooks.purchaseorders.ALL",
    "ZohoInventory.purchaseorders.CREATE",
    "ZohoInventory.purchaseorders.UPDATE",
    "ZohoInventory.purchaseorders.DELETE",
    "ZohoInventory.purchaseorders.ALL",
    "ZohoBooks.estimates.DELETE",
    "ZohoBooks.estimates.ALL",
    "ZohoInventory.estimates.CREATE",
    "ZohoInventory.estimates.UPDATE",
    "ZohoInventory.estimates.DELETE",
    "ZohoInventory.estimates.ALL",
    "ZohoBooks.fullaccess.all",
    "ZohoInventory.fullaccess.all",
)
REAUTHORIZE_STEPS = (
    "Run PREPARE_DADO_ZOHO_ACCESS.bat, create the one-time grant in the Zoho API "
    "Console with the printed scope list, then run REAUTHORIZE_DADO_ZOHO.bat and "
    "CHECK_DADO_ZOHO.bat."
)

PURCHASE_ORDER_PUT_PATH_RE = re.compile(r"^/books/v3/purchaseorders/([1-9][0-9]*)$")
ESTIMATE_PUT_PATH_RE = re.compile(r"^/books/v3/estimates/([1-9][0-9]*)$")
# The complete bounded read surface. Anything else is refused before it is sent.
READ_PATH_PATTERNS = (
    re.compile(r"^/books/v3/purchaseorders/[1-9][0-9]*$"),
    re.compile(r"^/books/v3/estimates/[1-9][0-9]*$"),
    re.compile(r"^/books/v3/settings/taxes$"),
)

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"^[1-9][0-9]*$")
CENT = Decimal("0.01")
MAX_TEXT = 4000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_LUG_RATE = Decimal("100000")

# ---------------------------------------------------------------------------
# The two fixed records. Read read-only on 2026-08-21 and pinned here; the tool
# still re-reads and re-fingerprints live state at BOTH staging and commit, so
# these constants are the identity gate, never a substitute for a live read.
# ---------------------------------------------------------------------------

ON_HST_TAX_ID = "96274000000035516"
ON_HST_TAX_NAME = "ON HST"
ON_HST_PERCENTAGE = Decimal("13")

PURCHASE_ORDER_TARGET = {
    "action": ACTION_PURCHASE_ORDER,
    "record": "purchase_order",
    "id_key": "purchaseorder_id",
    "number_key": "purchaseorder_number",
    "record_id": "96274000001598034",
    "record_number": "PO-00010",
    "reference_number": "J26-403-AIR-R1",
    "party_key": "vendor_id",
    "party_id": "96274000000027889",
    "party_name": "JRAIN FRP LIMITED",
    "currency_code": "USD",
    "status": "open",
    "is_emailed": True,
    "line_count": 7,
    "sub_total": "5942.00",
    "tax_total": "0.00",
    "total": "5942.00",
    "new_line_tax_id": "",
}
PURCHASE_ORDER_LINES = (
    ("96274000001598037", "96274000001555194", "21", "28"),
    ("96274000001598038", "96274000001600003", "5", "50"),
    ("96274000001598039", "96274000001555208", "8", "78"),
    ("96274000001598040", "96274000001555222", "3", "490"),
    ("96274000001598041", "96274000001555236", "3", "360"),
    ("96274000001598042", "96274000001600017", "1", "690"),
    ("96274000001598043", "96274000001603002", "2", "620"),
)

ESTIMATE_TARGET = {
    "action": ACTION_ESTIMATE,
    "record": "estimate",
    "id_key": "estimate_id",
    "number_key": "estimate_number",
    "record_id": "96274000001602028",
    "record_number": "QT-000034",
    "reference_number": "J26-403",
    "party_key": "customer_id",
    "party_id": "96274000000060001",
    "party_name": "Troy Dualam Inc.",
    "currency_code": "CAD",
    "status": "accepted",
    "is_emailed": None,
    "line_count": 7,
    "sub_total": "21391.20",
    "tax_total": "2780.86",
    "total": "24172.06",
    "new_line_tax_id": ON_HST_TAX_ID,
}
ESTIMATE_LINES = (
    ("96274000001602029", "96274000001555194", "21", "100.8"),
    ("96274000001602030", "96274000001600003", "5", "180"),
    ("96274000001602031", "96274000001555208", "8", "280.8"),
    ("96274000001602032", "96274000001555222", "3", "1764"),
    ("96274000001602033", "96274000001555236", "3", "1296"),
    ("96274000001602034", "96274000001600017", "1", "2484"),
    ("96274000001602035", "96274000001603002", "2", "2232"),
)

TARGETS = {
    ACTION_PURCHASE_ORDER: PURCHASE_ORDER_TARGET,
    ACTION_ESTIMATE: ESTIMATE_TARGET,
}
TARGET_LINES = {
    ACTION_PURCHASE_ORDER: PURCHASE_ORDER_LINES,
    ACTION_ESTIMATE: ESTIMATE_LINES,
}

# ---------------------------------------------------------------------------
# The two fixed additions.
# ---------------------------------------------------------------------------

TDI_MULTIPLIER = Decimal("3.6")
NEW_LINE_UNIT = "pcs"

DIP_TUBE_QUANTITY = Decimal("6")
DIP_TUBE_SUPPLIER_RATE_USD = Decimal("460.00")
DIP_TUBE_NAME = "Custom D441 Dip Tube per drawing (non-standard)"
DIP_TUBE_DESCRIPTION = (
    "Custom D441 dip tube per drawing DIP TUBE.pdf. Resin Derakane 441/MEKP. "
    "2.00 inch ID; 164.00 inch length. Total wall 0.65 inch, made up of a 0.20 inch CCMMMM "
    "inner liner, 0.25 inch structural and a 0.20 inch MMMMCC outer liner. "
    "13 holes on each side; 0.50 inch holes; 6 inches between holes in opposite directions. "
    "Non-standard item: no catalog SKU, and no Zoho Inventory item is created for it."
)

LIFTING_LUG_QUANTITY = Decimal("24")
LIFTING_LUG_NAME = (
    "Custom SS316L lifting lug / anchor clip per drawing, 22-inch bar (non-standard)"
)
LIFTING_LUG_DESCRIPTION = (
    "Lifting lug / anchor clip per drawing ANCHOR CLIPS.pdf; bar length 22 inches per the "
    "Operations email of 2026-08-21 14:08 UTC. MATERIAL -- BOTH SOURCES ARE RECORDED AS THEY "
    "STAND: the request specifies SS316L and the drawing itself states SS 316. Neither was "
    "changed to match the other. Plate bent round bar with square retainer welded both sides; "
    "1.63 inch hole; bar bent to radius R 3 ft 6 in. "
    "Non-standard item: no catalog SKU, and no Zoho Inventory item is created for it."
)

# ---------------------------------------------------------------------------
# Immutable source files. Re-hashed at staging AND at commit; a changed,
# replaced or missing source refuses for free.
# ---------------------------------------------------------------------------

SOURCE_DIR = (
    ROOT / "Dado" / "20_Working" / "pricing_requests" / "d441" / "j26_403_revision_sources"
)
FIXED_SOURCES = (
    {
        "key": "dip_tube_drawing",
        "name": "DIP TUBE.pdf",
        "sha256": "5f9ac494770c7c0193a2c08a32c47300ba927b2e339362ed8edd17e06d65df04",
        "role": "Dip tube geometry, laminate schedule, hole pattern and quantity 6.",
    },
    {
        "key": "dip_tube_quotation",
        "name": "Quotation of Dip Tube - revised.xlsx",
        "sha256": "d066ac0ab0a500623c9e46a45ba9beefa82bbb1f939ed647dadf0171ad5bb5fd",
        "role": "Fei revised quotation: 6 x USD 460.00 = USD 2,760.00.",
    },
    {
        "key": "lifting_lug_drawing",
        "name": "ANCHOR CLIPS.pdf",
        "sha256": "0e919b082b43cdb6201c1f9236dec85d4d94e4df7bc968ba50f8fb4c4f340364",
        "role": "Lifting lug / anchor clip geometry and quantity 24; states SS 316.",
    },
)
FIXED_SOURCE_DIGESTS = frozenset(entry["sha256"] for entry in FIXED_SOURCES)

# ---------------------------------------------------------------------------
# Everything the sources do NOT establish, disclosed rather than invented.
# ---------------------------------------------------------------------------

DISCLOSURES = (
    {
        "key": "dip_tube_air_shipment_cut_not_in_any_source",
        "statement": (
            "The request mentions cutting the dip tube into three sections for air shipment. "
            "NEITHER source establishes it: DIP TUBE.pdf carries no cut, section count, joint or "
            "shipping-split note anywhere, and the Fei workbook prices one line of 6 tubes with no "
            "sectioning. The cut is therefore NOT represented in the business lines -- the "
            "quantity stays 6 tubes, the description states the single 164.00 inch length the "
            "drawing gives, and no price was adjusted for it. Inventing a changed quantity to "
            "represent a cut this tool cannot see in a source is exactly what is refused here. If "
            "the cut must appear on the documents, it needs its own source and its own new plan."
        ),
    },
    {
        "key": "dip_tube_workbook_freight_cell_inconsistent",
        "statement": (
            "'Quotation of Dip Tube - revised.xlsx' is internally inconsistent on freight. Row 5 "
            "carries unit price USD 2,500.00 in cell E5 while that same row's line total F5 is "
            "USD 1,800.00 with no formula, and the sheet's own note reads 'The above shipping cost "
            "$1,800 is assuming that the tubes will be shipped together with other air shipping "
            "fittings.' The CIF total (=SUM(F4:F5)) uses 1,800, giving USD 4,560.00. THIS PLAN "
            "ADDS NO FREIGHT LINE AND MULTIPLIES NO FREIGHT FIGURE. The purchase order's existing "
            "notes already state an estimated USD 5,000 air freight, and this commission does not "
            "authorize changing that header or reconciling it against the workbook."
        ),
    },
    {
        "key": "lifting_lug_material_grade_two_sources_disagree",
        "statement": (
            "The email request specifies SS316L. The drawing ANCHOR CLIPS.pdf itself states "
            "'QTY: 24, SS 316'. Both are recorded verbatim in the line description and neither was "
            "silently rewritten to match the other. The business label uses SS316L because that is "
            "what was requested; the drawing reference is preserved so the discrepancy stays "
            "visible to the vendor and to Troy Dualam."
        ),
    },
    {
        "key": "lifting_lug_bar_length_is_email_not_drawing",
        "statement": (
            "The 22-inch bar length comes from the Operations email of 2026-08-21 14:08 UTC, not "
            "from the drawing. Every other lug dimension in the description is read off "
            "ANCHOR CLIPS.pdf."
        ),
    },
    {
        "key": "drawing_units_label_contradicts_its_own_figures",
        "statement": (
            "Both drawings print the boilerplate 'DIMENSIONS ARE IN MILLIMETERS' while their own "
            "figures are plainly inches (a 2.00 ID / 164.00 long dip tube, a radius given as "
            "R 3' 6\"). Every dimension in these descriptions is stated in inches, matching the "
            "figures rather than the boilerplate. This is recorded because it is a real ambiguity "
            "in the source, not because anything was converted."
        ),
    },
    {
        "key": "tdi_multiplier_is_a_pricing_rule_not_an_fx_conversion",
        "statement": (
            "The Troy Dualam rate is the supplier USD unit cost multiplied by 3.6, producing a CAD "
            "rate on a CAD estimate. That is Rachad's own pricing rule as commissioned; NO "
            "exchange rate is read, applied or implied, and the estimate's own live exchange rate "
            "is resent unchanged. The plan shows the input cost, the multiplier, the unrounded "
            "product and the posted rate so the arithmetic is checkable by eye."
        ),
    },
    {
        "key": "appended_line_order_is_left_to_zoho",
        "statement": (
            "The two appended lines deliberately carry no item_order. Every ORIGINAL line resends "
            "its own live item_order, so nothing existing can be renumbered by this tool. Where "
            "Zoho places the two new lines is Zoho's own behaviour; the read-back proves each "
            "appended line appears EXACTLY ONCE and that all seven original lines are unchanged in "
            "their original order, and locks the plan indeterminate if either is untrue."
        ),
    },
    {
        "key": "new_lines_unit_of_measure",
        "statement": (
            "Both appended lines carry unit 'pcs'. The Fei workbook states PCS for the dip tube "
            "line, and the lug drawing states 'QTY: 24', a piece count. No other unit is reachable."
        ),
    },
)

# ---------------------------------------------------------------------------
# CONTRACT FACTS. Not one of these is proven in this tree, so COMMIT refuses.
# Each entry says what it would take to prove it. Nothing here is decided by
# guessing at Zoho's behaviour, and no payload is invented to route around it.
# ---------------------------------------------------------------------------

CONTRACT_FACTS = {
    "purchase_order_update_route": {
        "proven": False,
        "statement": (
            "PUT /books/v3/purchaseorders/<id> accepts a full-record update and returns the "
            "updated purchase order."
        ),
        "why_unproven": (
            "No FRP Depot tool has ever issued a purchase-order PUT. zoho_purchase_order_tool.py "
            "is create-only -- its transport has no PUT verb at all -- so nothing in this tree "
            "records the accepted request shape or the response shape."
        ),
        "what_would_prove_it": (
            "Zoho's own published purchase-order update contract read and stored in this tree, or "
            "a recorded live request/response pair from a separately approved rehearsal."
        ),
    },
    "emailed_open_purchase_order_in_place_update": {
        "proven": False,
        "statement": (
            "An 'open' purchase order with is_emailed true accepts an in-place update without "
            "changing its status, its emailed state or re-sending it to the vendor."
        ),
        "why_unproven": (
            "PO-00010 was already emailed to Fei. Whether Zoho re-mails, re-opens, re-statuses or "
            "silently clears is_emailed on update is not established by anything measured here."
        ),
        "what_would_prove_it": (
            "A live update of a disposable emailed purchase order showing status and is_emailed "
            "unchanged and no mail sent, or Zoho's documented statement of the same."
        ),
    },
    "purchase_order_free_text_line_append": {
        "proven": False,
        "statement": (
            "A purchase-order update accepts an appended line carrying name, description, "
            "quantity, rate and unit with NO item_id and NO line_item_id, and does not create an "
            "Inventory item for it."
        ),
        "why_unproven": (
            "Every purchase-order line this organization has ever carried names an existing Zoho "
            "item. No free-text purchase-order line has been observed, accepted or rejected here."
        ),
        "what_would_prove_it": (
            "A live response showing the appended line stored with a blank item_id and no new "
            "item in the Inventory list, or Zoho's documented line contract."
        ),
    },
    "accepted_estimate_in_place_update": {
        "proven": False,
        "statement": (
            "An estimate in 'accepted' status accepts an in-place update that sends no status "
            "field, and remains 'accepted' afterwards."
        ),
        "why_unproven": (
            "The general revision action in zoho_customer_quote_tool.py deliberately restricts "
            "itself to 'draft' and 'sent' because no accepted-state behaviour was ever measured. "
            "Zoho may refuse the write, may silently drop the record back to 'draft', or may "
            "accept it cleanly; this tree cannot tell which."
        ),
        "what_would_prove_it": (
            "A live update of a disposable accepted estimate showing status unchanged, or Zoho's "
            "documented statement about updating accepted estimates."
        ),
    },
    "estimate_free_text_line_append": {
        "proven": False,
        "statement": (
            "An estimate update accepts an appended line carrying name, description, quantity, "
            "rate, unit and tax_id with NO item_id and NO line_item_id, and does not create an "
            "Inventory item for it."
        ),
        "why_unproven": (
            "revision_live_lines in zoho_customer_quote_tool.py refuses a line with no item_id "
            "outright, so no free-text estimate line has ever been sent or observed here."
        ),
        "what_would_prove_it": (
            "A live response showing the appended line stored with a blank item_id and no new "
            "item in the Inventory list, or Zoho's documented line contract."
        ),
    },
}
ACTION_REQUIRED_FACTS = {
    ACTION_PURCHASE_ORDER: (
        "purchase_order_update_route",
        "emailed_open_purchase_order_in_place_update",
        "purchase_order_free_text_line_append",
    ),
    ACTION_ESTIMATE: (
        "accepted_estimate_in_place_update",
        "estimate_free_text_line_append",
    ),
}

# ---------------------------------------------------------------------------
# Fingerprint scope.
# ---------------------------------------------------------------------------

# Header figures Zoho itself recomputes from the lines, plus pure telemetry.
# Each money figure is asserted by its own explicit rule after the PUT; EVERY
# other returned field stays inside the byte-exact protected fingerprint.
HEADER_DERIVED_KEYS = frozenset({
    "sub_total", "sub_total_inclusive_of_tax", "sub_total_exclusive_of_discount",
    "discount_total", "discount_amount", "discount_percent", "discount_applied_on_amount",
    "tax_total", "total", "taxes", "line_items", "total_quantity", "roundoff_value",
    "bcy_sub_total", "bcy_sub_total_exclusive_of_discount", "bcy_discount_total",
    "bcy_tax_total", "bcy_total",
    "last_modified_time", "updated_time", "last_modified_by_id",
    "uninvoiced_amount", "unbilled_amount",
    "is_viewed_by_client", "client_viewed_time", "last_viewed_time",
    "shipping_charge_exclusive_of_tax", "shipping_charge_inclusive_of_tax",
    "shipping_charge_exclusive_of_tax_formatted", "shipping_charge_inclusive_of_tax_formatted",
})
# Secure client links Zoho regenerates between otherwise identical GETs. This is
# the complete volatile allowlist; every other pre-write field must match the
# staged record byte-for-byte.
VOLATILE_KEYS = frozenset({"estimate_url", "purchaseorder_url", "invoice_url"})
LINE_DERIVED_KEYS = frozenset({
    "item_total", "item_total_inclusive_of_tax", "line_item_total", "tax_amount",
    "line_item_taxes", "discount_amount", "discount_amount_formatted", "discounts",
    "quantity_formatted", "rate_formatted", "item_total_formatted",
})
TAX_AMOUNT_KEYS = frozenset({"tax_amount", "tax_amount_formatted"})

# ---------------------------------------------------------------------------
# The closed input schema. The ONLY caller-supplied business value in the whole
# tool is the lifting-lug supplier unit cost, and it needs real evidence.
# ---------------------------------------------------------------------------

INPUT_KEYS = {"lifting_lug_supplier_unit_cost_usd", "operator_source_note"}
REQUIRED_INPUT_KEYS = {"lifting_lug_supplier_unit_cost_usd"}
LUG_COST_KEYS = {"value", "source", "artifact_path", "artifact_sha256"}
MIN_SOURCE_LENGTH = 25
# Sources that assert rather than cite. A price that arrives with one of these
# is a guess wearing a citation, and this tool will not post a guess to a
# vendor's purchase order or a customer's quote.
ASSERTED_ONLY_SOURCE_MARKERS = (
    "assume", "assumed", "assumption", "estimate only", "estimated cost", "approx",
    "approximate", "ballpark", "guess", "placeholder", "tbd", "to be confirmed",
    "to be determined", "not confirmed", "unconfirmed", "no quote", "pending quote",
    "awaiting quote", "verbal", "from memory", "i think", "should be about",
    "same as last time", "rough estimate",
)
# Sources so general that they identify no document at all.
GENERIC_SOURCES = frozenset({
    "supplier", "the supplier", "vendor", "the vendor", "fei", "jrain", "jrain frp",
    "jrain frp limited", "email", "the email", "quote", "the quote", "quotation",
    "the quotation", "supplier quote", "supplier quotation", "rachad", "operations",
    "drawing", "the drawing", "attachment", "the attachment", "source", "see attached",
    "per email", "per quote", "per supplier", "as discussed", "as agreed", "n/a", "na",
    "none", "unknown", "-",
})
NAMED_REFUSALS = {
    "quantity": "Both quantities are fixed by this commission: 6 dip tubes and 24 lifting lugs.",
    "quantities": "Both quantities are fixed by this commission: 6 dip tubes and 24 lifting lugs.",
    "dip_tube_rate": "The dip tube rate is fixed at USD 460.00 from the Fei workbook.",
    "dip_tube_cost": "The dip tube rate is fixed at USD 460.00 from the Fei workbook.",
    "rate": "No line rate is caller-supplied except the one lifting-lug supplier unit cost.",
    "multiplier": "The Troy Dualam multiplier is fixed at 3.6 by this commission.",
    "description": "Both line descriptions are fixed constants built from the pinned sources.",
    "name": "Both line names are fixed constants.",
    "item_id": "The appended lines are non-catalog free-text lines and carry no item_id.",
    "line_item_id": "The appended lines are new; Zoho assigns their line_item_id.",
    "sku": "The appended lines are non-catalog lines and carry no SKU.",
    "tax_id": (
        "Tax is fixed: the estimate reuses the live ON HST 13% id and the purchase order lines "
        "carry none."
    ),
    "estimate_id": "The estimate is fixed: QT-000034 and nothing else.",
    "purchaseorder_id": "The purchase order is fixed: PO-00010 and nothing else.",
    "customer_id": "The customer is fixed and preserved from the live record.",
    "vendor_id": "The vendor is fixed and preserved from the live record.",
    "status": "No status field is sent by this tool at any point.",
    "reference_number": "Both reference numbers are preserved, never set.",
    "date": "No date is changed by this tool.",
    "notes": "Header notes are preserved, never changed.",
    "terms": "Header terms are preserved, never changed.",
    "freight": (
        "No freight line is added and no freight figure is changed. See the disclosed workbook "
        "inconsistency."
    ),
    "shipping": "No freight line is added and no freight figure is changed.",
    "currency_code": "The live currency is preserved and never set.",
    "exchange_rate": "The live exchange rate is preserved and never set.",
    "custom_fields": "Custom fields are outside this commission.",
    "attachment": "This tool cannot attach anything to any record.",
    "documents": "This tool cannot attach anything to any record.",
    "email": "This tool has no mail transport of any kind.",
    "send": "This tool has no mail transport of any kind.",
    "action": "There is no lifecycle, status or conversion action anywhere in this tool.",
    "template_id": "The live template is preserved, never set.",
    "create_item": "This tool never creates a Zoho item; the additions are free-text lines.",
}

RISK_NOTE = (
    "One PUT to ONE existing Zoho Books record, appending exactly the two fixed non-catalog lines "
    "named in this plan and changing nothing else. Every original line is resent once in live "
    "order with its own line_item_id and item_id, and every header value is the preserved live "
    "value; no status field is sent at all. THE TWO ACTIONS ARE INDEPENDENT: this plan moves ONE "
    "record, one APPROVED answers only this plan, and the other record needs its own plan and its "
    "own approval. THIS IS NOT REVERSIBLE FROM HERE -- there is no second PUT, no rollback, no "
    "delete, no restatus and no cleanup route in this tool by design. The plan is locked before "
    "the PUT, attempted once, and stays locked on any failure, timeout or indeterminate result. "
    "No email is sent; this tool has no mail transport."
)


class J26RevisionToolError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Small shared helpers, following the conventions of the commissioned Zoho tools
# ---------------------------------------------------------------------------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise J26RevisionToolError("Zoho returned evidence that is not JSON serializable.") from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise J26RevisionToolError(f"Input JSON is unreadable: {path}") from exc


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise J26RevisionToolError(f"Input file is unreadable: {path}") from exc


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def live_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise J26RevisionToolError(f"{label} is missing or not a number.")
    text = str(value).strip()
    if not text:
        raise J26RevisionToolError(f"{label} is blank.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise J26RevisionToolError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite():
        raise J26RevisionToolError(f"{label} must be a finite number.")
    return result


def number_json(value: Decimal) -> Any:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def parse_plan_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise J26RevisionToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise J26RevisionToolError(f"Plan {label} must include a timezone.")
    return parsed


# Shared owner authority (autonomy programme 2026-08-21, spec A3/A4/A5). Both
# records here are MONEY work (an emailed purchase order, an accepted quote):
# the two-step stays -- stage, then his own unambiguous go to THAT plan, sent
# AFTER the plan was written. Exact APPROVED is no longer REQUIRED. His
# "Proceed" of 2026-08-21 authorised the BUILD because it came before any plan
# existed; the timestamp rule below is what keeps that true, not the wording.
# A failed commit is reported and re-staged; nothing is permanently locked.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402


def require_exact_approval(approval: Any, plan: dict[str, Any], *,
                           lane: Any = None, sent_utc: Any = None) -> owner_authority.OwnerGo:
    """Rachad's own unambiguous go to THIS plan, sent after it was written (A3).

    It must come from his own message answering THIS plan (Hard Rule 3).
    Commissioning the tool is not approval, staging is not approval, and Dado
    cannot supply it. The time of his message (--approval-message-utc) is
    required: a word sent before the plan existed (his 'Proceed' to build)
    cannot approve it. The plan is mandatory; a money check never falls back
    to the reversible rule.
    """
    try:
        return owner_authority.require_owner_go_after_plan(
            approval, plan_created_utc=plan.get("created_utc"), plan_expires_utc=plan.get("expires_utc"),
            sent_utc=sent_utc, lane=lane, what="this J26-403 revision plan",
        )
    except owner_authority.OwnerAuthorityRefused as exc:
        raise J26RevisionToolError(str(exc)) from exc


def require_action(action: Any) -> str:
    if action not in TARGETS:
        raise J26RevisionToolError(
            "REFUSED: this tool reaches exactly two fixed actions, "
            f"{ACTION_PURCHASE_ORDER} and {ACTION_ESTIMATE}, and nothing else."
        )
    return str(action)


def books_organization_id(vault: dict[str, Any]) -> str:
    value = str(vault.get("books_organization_id") or "")
    if not ID_RE.fullmatch(value):
        raise J26RevisionToolError(
            "The saved Zoho connection has no FRP Depot Books organization ID."
        )
    return value


def require_update_scopes(action: str, scopes: list[str]) -> None:
    """The one narrow update scope present, every widening scope absent."""
    require_action(action)
    zoho_tool.validate_scopes(scopes)
    held = set(scopes)
    widened = sorted(held & set(FORBIDDEN_SCOPES))
    if widened:
        raise J26RevisionToolError(
            "REFUSED: the saved Zoho connection holds scope(s) this tool was never commissioned "
            "to have: " + ", ".join(widened) + ". No Zoho call was made."
        )
    needed = ACTION_UPDATE_SCOPES[action]
    if needed not in held:
        raise J26RevisionToolError(
            f"REFUSED: the saved Zoho connection lacks {needed}, so the {action} cannot be "
            "written. " + REAUTHORIZE_STEPS + " No PUT was issued."
        )


def contract_status(action: str) -> dict[str, Any]:
    """The proof state of every contract fact this action depends on."""
    require_action(action)
    facts = []
    for key in ACTION_REQUIRED_FACTS[action]:
        fact = CONTRACT_FACTS[key]
        facts.append({
            "key": key,
            "proven": bool(fact["proven"]),
            "statement": fact["statement"],
            "why_unproven": "" if fact["proven"] else fact["why_unproven"],
            "what_would_prove_it": "" if fact["proven"] else fact["what_would_prove_it"],
        })
    unproven = [fact["key"] for fact in facts if not fact["proven"]]
    return {
        "facts": facts,
        "unproven": unproven,
        "all_proven": not unproven,
        "commit_blocked": bool(unproven),
    }


def require_proven_contract(action: str) -> None:
    """Refused BEFORE the replay lock, the vault, the token and the network."""
    status = contract_status(action)
    if status["all_proven"]:
        return
    detail = " ".join(
        f"[{fact['key']}] {fact['why_unproven']} Proving it needs: {fact['what_would_prove_it']}"
        for fact in status["facts"] if not fact["proven"]
    )
    raise J26RevisionToolError(
        f"REFUSED: this build cannot commit the {action} because Zoho's contract for it is not "
        f"proven here. {detail} Nothing was locked, no token was read and no network call was "
        "made. This tool will not invent a payload shape, will not create dummy Zoho items as a "
        "workaround, and will not retry into a live record to find out. Prove the contract first, "
        "then stage a fresh plan."
    )


# ---------------------------------------------------------------------------
# Source evidence -- the three immutable files, re-hashed every single time
# ---------------------------------------------------------------------------


def contained_source(raw_path: Any, label: str) -> Path:
    """An absolute, non-symlinked file inside the one fixed source folder."""
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise J26RevisionToolError(
            f"REFUSED: {label} must be an absolute path inside {SOURCE_DIR}."
        )
    lexical_root = SOURCE_DIR.absolute()
    try:
        candidate.absolute().relative_to(lexical_root)
    except ValueError as exc:
        raise J26RevisionToolError(
            f"REFUSED: {label} is outside the one allowlisted source folder {SOURCE_DIR}."
        ) from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise J26RevisionToolError(
                f"REFUSED: {label} and its parents must not be symlinks."
            )
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise J26RevisionToolError(f"REFUSED: {label} is outside the source folder.")
        cursor = parent
    try:
        root = SOURCE_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise J26RevisionToolError(
            f"REFUSED: {label} does not resolve to an existing file inside {SOURCE_DIR}."
        ) from exc
    if root not in resolved.parents or not resolved.is_file():
        raise J26RevisionToolError(f"REFUSED: {label} is not a file inside {SOURCE_DIR}.")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise J26RevisionToolError(f"REFUSED: {label} cannot be measured.") from exc
    if size <= 0:
        raise J26RevisionToolError(f"REFUSED: {label} is empty, so it evidences nothing.")
    if size > MAX_SOURCE_BYTES:
        raise J26RevisionToolError(
            f"REFUSED: {label} is larger than the {MAX_SOURCE_BYTES}-byte ceiling."
        )
    return resolved


def fixed_source_evidence() -> list[dict[str, Any]]:
    """Re-hash all three pinned sources. Any drift refuses before anything else."""
    evidence: list[dict[str, Any]] = []
    for entry in FIXED_SOURCES:
        path = SOURCE_DIR / entry["name"]
        if not path.is_file():
            raise J26RevisionToolError(
                f"REFUSED: the immutable source {entry['name']} is missing from {SOURCE_DIR}. "
                "Nothing staged."
            )
        actual = file_digest(path)
        if actual != entry["sha256"]:
            raise J26RevisionToolError(
                f"REFUSED: the immutable source {entry['name']} is sha256 {actual}, not the "
                f"pinned {entry['sha256']}. The evidence this plan rests on changed. Nothing "
                "staged."
            )
        evidence.append({
            "key": entry["key"],
            "name": entry["name"],
            "path": str(path),
            "sha256": actual,
            "bytes": path.stat().st_size,
            "role": entry["role"],
        })
    return evidence


def require_fixed_source_evidence(evidence: Any) -> None:
    """The recorded source projection, re-checked wherever it comes from."""
    if not isinstance(evidence, list) or len(evidence) != len(FIXED_SOURCES):
        raise J26RevisionToolError("Plan source evidence is not the exact fixed set.")
    for entry, recorded in zip(FIXED_SOURCES, evidence):
        if not isinstance(recorded, dict):
            raise J26RevisionToolError("Plan source evidence carries an unreadable entry.")
        if set(recorded) != {"key", "name", "path", "sha256", "bytes", "role"}:
            raise J26RevisionToolError("Plan source evidence is not the exact closed schema.")
        if recorded["key"] != entry["key"] or recorded["name"] != entry["name"]:
            raise J26RevisionToolError("Plan source evidence names a different source file.")
        if recorded["sha256"] != entry["sha256"]:
            raise J26RevisionToolError(
                f"Plan source evidence for {entry['name']} carries a digest that is not the "
                "pinned one."
            )


def clean_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise J26RevisionToolError(f"REFUSED: {label} must be text.")
    if value != value.strip():
        raise J26RevisionToolError(f"REFUSED: {label} must not be padded with whitespace.")
    if not value:
        raise J26RevisionToolError(f"REFUSED: {label} must be nonblank.")
    if len(value) > MAX_TEXT:
        raise J26RevisionToolError(f"REFUSED: {label} is longer than {MAX_TEXT} characters.")
    return value


def require_concrete_source(value: Any, label: str) -> str:
    """A source must CITE a document, not assert a number.

    Blank, generic and asserted-only sources are refused by name so the message
    says what is wrong instead of only 'invalid source'.
    """
    text = clean_text(value, label)
    folded = " ".join(text.split()).casefold()
    if folded in GENERIC_SOURCES:
        raise J26RevisionToolError(
            f"REFUSED: {label} is {text!r}, which names no particular document. Cite the actual "
            "quotation -- who sent it, when, and what it says -- so the price can be checked "
            "against something."
        )
    if len(text) < MIN_SOURCE_LENGTH:
        raise J26RevisionToolError(
            f"REFUSED: {label} is too short to identify a document ({len(text)} characters; at "
            f"least {MIN_SOURCE_LENGTH} are needed). Cite the actual quotation."
        )
    for marker in ASSERTED_ONLY_SOURCE_MARKERS:
        if marker in folded:
            raise J26RevisionToolError(
                f"REFUSED: {label} contains {marker!r}, so it asserts a price rather than citing "
                "one. This tool will not post an assumed, approximate or remembered cost to a "
                "vendor's purchase order or a customer's quote. Get the real quotation first."
            )
    if not any(character.isdigit() for character in text):
        raise J26RevisionToolError(
            f"REFUSED: {label} carries no date, amount or document number, so it cannot locate a "
            "specific quotation. Cite the actual quotation."
        )
    return text


def lug_cost_intent(raw: Any) -> dict[str, Any]:
    """The closed input schema, normalized once so stage and commit agree."""
    if not isinstance(raw, dict):
        raise J26RevisionToolError("REFUSED: the input must be one JSON object.")
    for key in sorted(set(raw) & set(NAMED_REFUSALS)):
        raise J26RevisionToolError(f"REFUSED: the input names {key}. {NAMED_REFUSALS[key]}")
    unknown = sorted(set(raw) - INPUT_KEYS)
    if unknown:
        raise J26RevisionToolError(
            "REFUSED: the input names uncommissioned field(s): " + ", ".join(unknown)
            + ". The complete schema is: " + ", ".join(sorted(INPUT_KEYS))
            + ". Every other business value in this tool is a fixed constant or a fresh live read."
        )
    missing = sorted(REQUIRED_INPUT_KEYS - set(raw))
    if missing:
        raise J26RevisionToolError(
            "REFUSED: the input is missing " + ", ".join(missing)
            + ". The lifting-lug supplier unit cost is NOT known to this tool and is never "
            "guessed: no live Fei quotation carrying it existed at the last read. Neither plan "
            "stages without it."
        )
    entry = raw["lifting_lug_supplier_unit_cost_usd"]
    if not isinstance(entry, dict) or set(entry) != LUG_COST_KEYS:
        raise J26RevisionToolError(
            "REFUSED: lifting_lug_supplier_unit_cost_usd must be exactly "
            "{\"value\": ..., \"source\": \"...\", \"artifact_path\": \"...\", "
            "\"artifact_sha256\": \"...\"}. An asserted price with no artifact is refused."
        )
    cost = live_decimal(entry["value"], "lifting_lug_supplier_unit_cost_usd.value")
    if cost <= 0:
        raise J26RevisionToolError(
            "REFUSED: lifting_lug_supplier_unit_cost_usd.value must be greater than zero. A zero "
            "cost would price the Troy Dualam line at zero."
        )
    if cost > MAX_LUG_RATE:
        raise J26RevisionToolError(
            f"REFUSED: lifting_lug_supplier_unit_cost_usd.value exceeds the {MAX_LUG_RATE} "
            "ceiling; that is not a lifting-lug unit cost."
        )
    if -cost.as_tuple().exponent > 6:
        raise J26RevisionToolError(
            "REFUSED: lifting_lug_supplier_unit_cost_usd.value carries more than six decimals."
        )
    source = require_concrete_source(
        entry["source"], "lifting_lug_supplier_unit_cost_usd.source"
    )
    stated_digest = clean_text(
        entry["artifact_sha256"], "lifting_lug_supplier_unit_cost_usd.artifact_sha256"
    ).casefold()
    if not HEX_64_RE.fullmatch(stated_digest):
        raise J26RevisionToolError(
            "REFUSED: lifting_lug_supplier_unit_cost_usd.artifact_sha256 must be 64 lowercase hex "
            "characters."
        )
    artifact = contained_source(
        entry["artifact_path"], "lifting_lug_supplier_unit_cost_usd.artifact_path"
    )
    actual_digest = file_digest(artifact)
    if actual_digest != stated_digest:
        raise J26RevisionToolError(
            f"REFUSED: the lifting-lug price artifact {artifact.name} is sha256 {actual_digest}, "
            f"not the stated {stated_digest}. The file and the claim about it disagree, so the "
            "price is not evidenced. Nothing staged."
        )
    if actual_digest in FIXED_SOURCE_DIGESTS:
        raise J26RevisionToolError(
            f"REFUSED: {artifact.name} is one of the three pinned J26-403 sources, and NONE of "
            "them carries a lifting-lug price: the two PDFs are drawings and the workbook prices "
            "only the dip tube (6 x USD 460 plus freight). Pointing at one of them does not "
            "evidence a lug cost. Nothing staged."
        )
    return {
        "lifting_lug_supplier_unit_cost_usd": {
            "value": format(cost, "f"),
            "source": source,
            "artifact_name": artifact.name,
            "artifact_path": str(artifact),
            "artifact_sha256": actual_digest,
            "artifact_bytes": artifact.stat().st_size,
        },
        "operator_source_note": (
            clean_text(raw["operator_source_note"], "operator_source_note")
            if "operator_source_note" in raw else ""
        ),
    }


def require_live_lug_artifact(intent: dict[str, Any]) -> None:
    """Re-prove the price artifact at commit. A vanished source refuses for free."""
    entry = intent["lifting_lug_supplier_unit_cost_usd"]
    artifact = contained_source(
        entry["artifact_path"], "lifting_lug_supplier_unit_cost_usd.artifact_path"
    )
    actual = file_digest(artifact)
    if actual != entry["artifact_sha256"]:
        raise J26RevisionToolError(
            f"REFUSED: the lifting-lug price artifact {artifact.name} is now sha256 {actual}, not "
            f"the reviewed {entry['artifact_sha256']}. The evidence changed after review. No PUT "
            "was issued and this plan is not locked; stage a new plan."
        )


# ---------------------------------------------------------------------------
# Read-only Zoho access, all through zoho_tool's GET-only helper
# ---------------------------------------------------------------------------


def require_read_path(path: str) -> str:
    if not any(pattern.fullmatch(path.split("?", 1)[0]) for pattern in READ_PATH_PATTERNS):
        raise J26RevisionToolError(
            f"REFUSED: {path.split('?', 1)[0]} is not one of this tool's bounded read routes."
        )
    return path


def api_get(
    access_token: str, vault: dict[str, Any], path: str, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The ONE read path in this module: GET only, route-allowlisted."""
    require_read_path(path)
    parameters = dict(query or {})
    parameters["organization_id"] = books_organization_id(vault)
    return zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"{path}?{urlencode(parameters)}"
    )


def record_path(action: str, record_id: str) -> str:
    require_action(action)
    if not ID_RE.fullmatch(str(record_id)):
        raise J26RevisionToolError("REFUSED: the record ID is invalid.")
    if action == ACTION_PURCHASE_ORDER:
        return f"/books/v3/purchaseorders/{record_id}"
    return f"/books/v3/estimates/{record_id}"


def record_envelope_key(action: str) -> str:
    return "purchaseorder" if action == ACTION_PURCHASE_ORDER else "estimate"


def get_record(access_token: str, vault: dict[str, Any], action: str) -> dict[str, Any]:
    """Read the ONE fixed record this action targets. No list, no search."""
    target = TARGETS[require_action(action)]
    record_id = target["record_id"]
    result = api_get(access_token, vault, record_path(action, record_id))
    record = result.get(record_envelope_key(action))
    if not isinstance(record, dict) or str(record.get(target["id_key"]) or "") != record_id:
        raise J26RevisionToolError(
            f"Zoho returned no {target['record']} record for {record_id}."
        )
    return json_copy(record)


def get_active_taxes(access_token: str, vault: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = api_get(access_token, vault, "/books/v3/settings/taxes")
    rows = result.get("taxes")
    if not isinstance(rows, list):
        raise J26RevisionToolError("Zoho returned no readable tax list. Nothing staged.")
    taxes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise J26RevisionToolError("Zoho returned an unreadable tax row. Nothing staged.")
        tax_id = str(row.get("tax_id") or "")
        if not ID_RE.fullmatch(tax_id):
            continue
        taxes[tax_id] = {
            "tax_id": tax_id,
            "tax_name": str(row.get("tax_name") or ""),
            "tax_percentage": format(
                live_decimal(row.get("tax_percentage"), f"tax {tax_id} percentage"), "f"
            ),
            "tax_type": str(row.get("tax_type") or ""),
            "status": str(row.get("status") or ""),
            "is_inactive": bool(row.get("is_inactive")),
        }
    return taxes


def on_hst_evidence(taxes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The one tax the appended estimate lines reuse -- the estimate's own."""
    row = taxes.get(ON_HST_TAX_ID)
    if row is None:
        raise J26RevisionToolError(
            f"REFUSED: tax {ON_HST_TAX_ID} ({ON_HST_TAX_NAME}) is not an active tax in this Zoho "
            "organization. This tool cannot create a tax. Nothing staged."
        )
    if row["is_inactive"] or row["status"].casefold() != "active":
        raise J26RevisionToolError(
            f"REFUSED: tax {ON_HST_TAX_ID} is not active. Nothing staged."
        )
    if row["tax_type"] != "tax":
        raise J26RevisionToolError(
            f"REFUSED: tax {ON_HST_TAX_ID} is a {row['tax_type']}, not a simple tax; its component "
            "rounding is not predictable here. Nothing staged."
        )
    if Decimal(row["tax_percentage"]) != ON_HST_PERCENTAGE:
        raise J26RevisionToolError(
            f"REFUSED: tax {ON_HST_TAX_ID} is now {row['tax_percentage']}%, not the "
            f"{ON_HST_PERCENTAGE}% this plan prices. Nothing staged."
        )
    return {ON_HST_TAX_ID: json_copy(row)}


# ---------------------------------------------------------------------------
# The live record: exact identity, an eligible state, and no disqualifying link
# ---------------------------------------------------------------------------

# Every state that would mean the record is beyond a plain in-place line append.
DISQUALIFYING_PURCHASE_ORDER_STATUSES = (
    "draft", "pending_approval", "approved", "billed", "partially_billed", "closed",
    "cancelled", "void", "deleted", "partially_received", "received",
)
DISQUALIFYING_ESTIMATE_STATUSES = (
    "draft", "sent", "declined", "expired", "invoiced", "void", "deleted", "cancelled",
)
# Live links that mean this record has already moved downstream. Any one of them
# refuses BEFORE a lock and BEFORE a write.
PURCHASE_ORDER_LINK_KEYS = (
    "bills", "purchasereceives", "purchase_receives", "billed_line_items",
    "receives", "salesorder_id", "salesorders",
)
ESTIMATE_LINK_KEYS = (
    "invoice_ids", "invoices", "salesorders", "salesorder_ids", "packages", "shipments",
    "payments", "creditnotes", "invoiced_amount",
)

PUT_HEADER_KEYS = {
    ACTION_PURCHASE_ORDER: (
        "vendor_id", "purchaseorder_number", "reference_number", "date", "delivery_date",
        "ship_via", "notes", "terms",
    ),
    ACTION_ESTIMATE: (
        "customer_id", "estimate_number", "reference_number", "date", "expiry_date",
        "notes", "terms",
    ),
}
# Control keys exist so a full-record PUT cannot silently drop a template,
# salesperson, currency or discount flag by omission. Each is sent ONLY when the
# live record actually carries it, so nothing is ever invented.
PUT_STRING_CONTROL_KEYS = ("currency_id", "template_id", "salesperson_id", "discount_type")
PUT_BOOL_CONTROL_KEYS = ("is_discount_before_tax", "is_inclusive_tax")
PUT_NUMBER_CONTROL_KEYS = ("exchange_rate",)
PUT_LIST_CONTROL_KEYS = ("contact_persons",)
ALLOWED_PUT_KEYS = {
    action: (
        set(PUT_HEADER_KEYS[action])
        | set(PUT_STRING_CONTROL_KEYS)
        | set(PUT_BOOL_CONTROL_KEYS)
        | set(PUT_NUMBER_CONTROL_KEYS)
        | set(PUT_LIST_CONTROL_KEYS)
        | {"line_items"}
    )
    for action in (ACTION_PURCHASE_ORDER, ACTION_ESTIMATE)
}
REQUIRED_PUT_KEYS = {
    ACTION_PURCHASE_ORDER: {"vendor_id", "purchaseorder_number", "date", "line_items"},
    ACTION_ESTIMATE: {"customer_id", "estimate_number", "date", "line_items"},
}
EXISTING_LINE_PUT_KEYS = (
    "line_item_id", "item_id", "name", "description", "quantity", "rate", "unit",
    "item_order", "tax_id", "discount",
)
REQUIRED_EXISTING_LINE_PUT_KEYS = {"line_item_id", "item_id", "name", "quantity", "rate"}
NEW_LINE_PUT_KEYS = {
    ACTION_PURCHASE_ORDER: ("name", "description", "quantity", "rate", "unit"),
    ACTION_ESTIMATE: ("name", "description", "quantity", "rate", "unit", "tax_id"),
}
REQUIRED_NEW_LINE_PUT_KEYS = {"name", "description", "quantity", "rate", "unit"}
# Keys an appended line must NEVER carry. item_id and line_item_id would link or
# claim a catalog record; the rest are item, stock or Inventory fields that have
# no business on a free-text line.
FORBIDDEN_NEW_LINE_KEYS = (
    "item_id", "line_item_id", "sku", "product_id", "item_order", "account_id",
    "warehouse_id", "warehouse_name", "hsn_or_sac", "item_custom_fields", "purchase_rate",
    "stock_on_hand", "available_stock", "quantity_input", "item_type", "product_type",
    "is_linked_with_zohocrm", "project_id", "time_entry_ids", "tags", "discount",
    "salesorder_item_id", "purchaseorder_item_id",
)


def require_no_disqualifying_state(action: str, record: dict[str, Any]) -> dict[str, Any]:
    """Downstream links and lifecycle state, refused before any lock or write."""
    target = TARGETS[require_action(action)]
    observed: dict[str, Any] = {}
    keys = (
        PURCHASE_ORDER_LINK_KEYS if action == ACTION_PURCHASE_ORDER else ESTIMATE_LINK_KEYS
    )
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        empty = (
            value in (None, "", 0, 0.0, False)
            or (isinstance(value, list) and not value)
            or (isinstance(value, dict) and not value)
        )
        observed[key] = json_copy(value) if not empty else None
        if not empty:
            raise J26RevisionToolError(
                f"REFUSED: the live {target['record']} {target['record_number']} carries "
                f"{key}={value!r}, so it has already moved downstream. This tool appends two lines "
                "to a record that has not been invoiced, converted, packed, shipped, received, "
                "billed or paid, and it has no route that could unwind any of those. Nothing "
                "staged, nothing locked."
            )
    if action == ACTION_PURCHASE_ORDER:
        for key, allowed in (
            ("billed_status", ("unbilled", "")),
            ("received_status", ("pending", "to_be_received", "")),
        ):
            if key in record and str(record.get(key) or "").casefold() not in allowed:
                raise J26RevisionToolError(
                    f"REFUSED: the live purchase order {key} is {record.get(key)!r}, which is "
                    "beyond an open, unbilled, unreceived order. Nothing staged."
                )
            observed[key] = str(record.get(key) or "")
    else:
        invoiced = live_decimal(record.get("invoiced_amount", 0), "live invoiced_amount")
        if invoiced != 0:
            raise J26RevisionToolError(
                f"REFUSED: the live estimate reports invoiced_amount {invoiced}, so it has already "
                "been converted. Nothing staged."
            )
        observed["invoiced_amount"] = money_text(invoiced)
    for key in ("is_deleted", "is_void", "is_cancelled"):
        if record.get(key):
            raise J26RevisionToolError(
                f"REFUSED: the live {target['record']} reports {key} true. Nothing staged."
            )
    return observed


def live_original_lines(action: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    """The seven original lines, proven identical to the pinned baseline.

    Order, line_item_id, item_id, quantity and rate are all pinned. A dropped,
    reordered, substituted or repriced original line refuses here -- before a
    lock exists and before any write is possible.
    """
    target = TARGETS[require_action(action)]
    pinned = TARGET_LINES[action]
    lines = record.get("line_items")
    if not isinstance(lines, list):
        raise J26RevisionToolError(
            f"The live {target['record']} carries no readable line list. Nothing staged."
        )
    if len(lines) != target["line_count"]:
        raise J26RevisionToolError(
            f"REFUSED: the live {target['record_number']} carries {len(lines)} line(s), not the "
            f"{target['line_count']} this plan is built on. The record changed. Nothing staged."
        )
    seen: set[str] = set()
    orders: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, (line, expected) in enumerate(zip(lines, pinned)):
        label = f"live line {index + 1}"
        if not isinstance(line, dict):
            raise J26RevisionToolError(f"{label} is not an object. Nothing staged.")
        line_item_id = str(line.get("line_item_id") or "")
        item_id = str(line.get("item_id") or "")
        want_line_id, want_item_id, want_quantity, want_rate = expected
        if not ID_RE.fullmatch(line_item_id):
            raise J26RevisionToolError(f"{label} has no usable line_item_id. Nothing staged.")
        if line_item_id in seen:
            raise J26RevisionToolError(f"{label} repeats line_item_id {line_item_id}.")
        seen.add(line_item_id)
        if line_item_id != want_line_id:
            raise J26RevisionToolError(
                f"REFUSED: {label} is line_item_id {line_item_id}, not the pinned {want_line_id}. "
                "The original lines have been reordered, dropped or replaced. Nothing staged."
            )
        if not ID_RE.fullmatch(item_id) or item_id != want_item_id:
            raise J26RevisionToolError(
                f"REFUSED: {label} names item {item_id!r}, not the pinned {want_item_id}. "
                "Nothing staged."
            )
        if not str(line.get("name") or "").strip():
            raise J26RevisionToolError(f"{label} has no item name. Nothing staged.")
        quantity = live_decimal(line.get("quantity"), f"{label} quantity")
        rate = live_decimal(line.get("rate"), f"{label} rate")
        if quantity != Decimal(want_quantity) or rate != Decimal(want_rate):
            raise J26RevisionToolError(
                f"REFUSED: {label} is now quantity {quantity} at rate {rate}, not the pinned "
                f"{want_quantity} at {want_rate}. An original line changed after this plan was "
                "designed; this tool appends lines and never restates one. Nothing staged."
            )
        order = line.get("item_order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise J26RevisionToolError(f"{label} has no integer item_order. Nothing staged.")
        if order in orders:
            raise J26RevisionToolError(f"{label} repeats item_order {order}. Nothing staged.")
        orders.add(order)
        discount = live_decimal(line.get("discount", 0), f"{label} discount")
        if discount != 0:
            raise J26RevisionToolError(
                f"REFUSED: {label} carries a line discount of {discount}. This action has no "
                "commissioned representation for it and will not risk restating it. Nothing staged."
            )
        tax_id = str(line.get("tax_id") or "")
        if action == ACTION_ESTIMATE:
            if tax_id != ON_HST_TAX_ID:
                raise J26RevisionToolError(
                    f"REFUSED: {label} carries tax {tax_id!r}, not the estimate's own "
                    f"{ON_HST_TAX_ID} ({ON_HST_TAX_NAME}). Nothing staged."
                )
            percentage = live_decimal(line.get("tax_percentage", 0), f"{label} tax_percentage")
            if percentage != ON_HST_PERCENTAGE:
                raise J26RevisionToolError(
                    f"REFUSED: {label} is taxed at {percentage}%, not {ON_HST_PERCENTAGE}%. "
                    "Nothing staged."
                )
        elif tax_id:
            raise J26RevisionToolError(
                f"REFUSED: {label} carries tax {tax_id!r}, but no line on this purchase order is "
                "taxed and this action adds no tax. Nothing staged."
            )
        result.append(json_copy(line))
    return result


def validate_live_record(action: str, record: dict[str, Any]) -> dict[str, Any]:
    """Exact identity, the exact expected state, and a revisable shape."""
    target = TARGETS[require_action(action)]
    if str(record.get(target["id_key"]) or "") != target["record_id"]:
        raise J26RevisionToolError(
            f"REFUSED: the live {target['id_key']} is {record.get(target['id_key'])!r}, not the "
            f"fixed {target['record_id']}. This tool reaches one record per action and nothing "
            "else. Nothing staged."
        )
    if str(record.get(target["number_key"]) or "") != target["record_number"]:
        raise J26RevisionToolError(
            f"REFUSED: the live {target['number_key']} is {record.get(target['number_key'])!r}, "
            f"not the fixed {target['record_number']}. Nothing staged."
        )
    if str(record.get("reference_number") or "") != target["reference_number"]:
        raise J26RevisionToolError(
            f"REFUSED: the live reference is {record.get('reference_number')!r}, not the fixed "
            f"{target['reference_number']!r}. Nothing staged."
        )
    if str(record.get(target["party_key"]) or "") != target["party_id"]:
        raise J26RevisionToolError(
            f"REFUSED: the live {target['party_key']} is {record.get(target['party_key'])!r}, not "
            f"the fixed {target['party_id']} ({target['party_name']}). Nothing staged."
        )
    if str(record.get("currency_code") or "") != target["currency_code"]:
        raise J26RevisionToolError(
            f"REFUSED: the live currency is {record.get('currency_code')!r}, not the fixed "
            f"{target['currency_code']}. Nothing staged."
        )
    status = str(record.get("status") or "")
    disqualifying = (
        DISQUALIFYING_PURCHASE_ORDER_STATUSES if action == ACTION_PURCHASE_ORDER
        else DISQUALIFYING_ESTIMATE_STATUSES
    )
    if status.casefold() in disqualifying or status != target["status"]:
        raise J26RevisionToolError(
            f"REFUSED: {target['record_number']} is {status!r}, not the exact {target['status']!r} "
            "state this plan is built on. No status field is ever sent by this tool, and it will "
            "not append lines to a record whose lifecycle has moved. Nothing staged."
        )
    if target["is_emailed"] is not None and bool(record.get("is_emailed")) is not target["is_emailed"]:
        raise J26RevisionToolError(
            f"REFUSED: {target['record_number']} reports is_emailed "
            f"{record.get('is_emailed')!r}, not the expected {target['is_emailed']!r}. "
            "Nothing staged."
        )
    for key, label in (("shipping_charge", "shipping charge"), ("adjustment", "adjustment")):
        if key in record:
            value = live_decimal(record.get(key, 0), f"live {key}")
            if value != 0:
                raise J26RevisionToolError(
                    f"REFUSED: the live {target['record']} carries a {label} of {value}. This "
                    "action has no commissioned representation for it and will not risk restating "
                    "it. Nothing staged."
                )
    entity_discount = live_decimal(record.get("discount", 0), "live discount")
    if entity_discount != 0:
        raise J26RevisionToolError(
            f"REFUSED: the live {target['record']} carries an entity-level discount of "
            f"{entity_discount}. Nothing staged."
        )
    discount_type = str(record.get("discount_type") or "")
    if discount_type and discount_type != "item_level":
        raise J26RevisionToolError(
            f"REFUSED: the live discount_type is {discount_type!r}, which this action does not "
            "know how to preserve. Nothing staged."
        )
    for key in ("sub_total", "tax_total", "total"):
        actual = live_decimal(record.get(key, 0), f"live {key}")
        if actual != Decimal(target[key]):
            raise J26RevisionToolError(
                f"REFUSED: the live {key} is {actual}, not the {target[key]} this plan is built "
                "on. The record changed. Nothing staged."
            )
    return require_no_disqualifying_state(action, record)


# ---------------------------------------------------------------------------
# Independent Decimal arithmetic
# ---------------------------------------------------------------------------


def original_rows(action: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every original line, unchanged, with its own recomputed line total."""
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        quantity = live_decimal(line.get("quantity"), f"original line {index + 1} quantity")
        rate = live_decimal(line.get("rate"), f"original line {index + 1} rate")
        rows.append({
            "position": index + 1,
            "origin": "original_preserved",
            "line_item_id": str(line.get("line_item_id") or ""),
            "item_id": str(line.get("item_id") or ""),
            "item_order": line.get("item_order"),
            "name": str(line.get("name") or ""),
            "description": str(line.get("description") or ""),
            "unit": str(line.get("unit") or ""),
            "quantity": format(quantity, "f"),
            "rate": format(rate, "f"),
            "tax_id": str(line.get("tax_id") or ""),
            "item_total": money_text((quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)),
        })
    return rows


def lug_pricing(action: str, intent: dict[str, Any]) -> dict[str, Any]:
    """The 3.6 arithmetic, shown in full so it is checkable by eye."""
    entry = intent["lifting_lug_supplier_unit_cost_usd"]
    cost = Decimal(entry["value"])
    if action == ACTION_PURCHASE_ORDER:
        return {
            "supplier_unit_cost_usd": format(cost, "f"),
            "multiplier_applied": False,
            "multiplier": format(TDI_MULTIPLIER, "f"),
            "unrounded": format(cost, "f"),
            "posted_rate": money_text(cost),
            "rounding": "half_up_to_two_decimals",
            "source": entry["source"],
            "artifact_name": entry["artifact_name"],
            "artifact_sha256": entry["artifact_sha256"],
        }
    unrounded = cost * TDI_MULTIPLIER
    return {
        "supplier_unit_cost_usd": format(cost, "f"),
        "multiplier_applied": True,
        "multiplier": format(TDI_MULTIPLIER, "f"),
        "unrounded": format(unrounded, "f"),
        "posted_rate": money_text(unrounded),
        "rounding": "half_up_to_two_decimals",
        "source": entry["source"],
        "artifact_name": entry["artifact_name"],
        "artifact_sha256": entry["artifact_sha256"],
    }


def dip_tube_pricing(action: str) -> dict[str, Any]:
    cost = DIP_TUBE_SUPPLIER_RATE_USD
    if action == ACTION_PURCHASE_ORDER:
        return {
            "supplier_unit_cost_usd": format(cost, "f"),
            "multiplier_applied": False,
            "multiplier": format(TDI_MULTIPLIER, "f"),
            "unrounded": format(cost, "f"),
            "posted_rate": money_text(cost),
            "rounding": "half_up_to_two_decimals",
            "source": "Fei revised quotation 'Quotation of Dip Tube - revised.xlsx', row 4: 6 PCS x USD 460.00 = USD 2,760.00.",
            "artifact_name": FIXED_SOURCES[1]["name"],
            "artifact_sha256": FIXED_SOURCES[1]["sha256"],
        }
    unrounded = cost * TDI_MULTIPLIER
    return {
        "supplier_unit_cost_usd": format(cost, "f"),
        "multiplier_applied": True,
        "multiplier": format(TDI_MULTIPLIER, "f"),
        "unrounded": format(unrounded, "f"),
        "posted_rate": money_text(unrounded),
        "rounding": "half_up_to_two_decimals",
        "source": "Fei revised quotation 'Quotation of Dip Tube - revised.xlsx', row 4: USD 460.00 each.",
        "artifact_name": FIXED_SOURCES[1]["name"],
        "artifact_sha256": FIXED_SOURCES[1]["sha256"],
    }


def appended_rows(action: str, intent: dict[str, Any], start: int) -> list[dict[str, Any]]:
    """The two fixed non-catalog lines, once each, in a fixed order."""
    target = TARGETS[require_action(action)]
    tax_id = target["new_line_tax_id"]
    rows: list[dict[str, Any]] = []
    for offset, (key, name, description, quantity, pricing) in enumerate((
        (
            "dip_tube", DIP_TUBE_NAME, DIP_TUBE_DESCRIPTION, DIP_TUBE_QUANTITY,
            dip_tube_pricing(action),
        ),
        (
            "lifting_lug", LIFTING_LUG_NAME, LIFTING_LUG_DESCRIPTION, LIFTING_LUG_QUANTITY,
            lug_pricing(action, intent),
        ),
    )):
        rate = Decimal(pricing["posted_rate"])
        rows.append({
            "position": start + offset + 1,
            "origin": "appended_non_catalog",
            "key": key,
            "line_item_id": "",
            "item_id": "",
            "item_order": None,
            "name": name,
            "description": description,
            "unit": NEW_LINE_UNIT,
            "quantity": format(quantity, "f"),
            "rate": format(rate, "f"),
            "tax_id": tax_id,
            "item_total": money_text((quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)),
            "pricing": pricing,
        })
    return rows


def totals_for(rows: list[dict[str, Any]], taxes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Bucket tax on the bucket net, cross-checked against a per-line sum.

    Zoho's own live figure for this record is what settles which method it uses;
    build_revision corroborates it before any figure here is asserted.
    """
    zero = Decimal("0")
    sub_total = zero
    buckets: dict[str, Decimal] = {}
    per_line_tax = zero
    untaxed = 0
    uncertain: list[str] = []
    for row in rows:
        net = Decimal(row["item_total"])
        sub_total += net
        tax_id = row["tax_id"]
        if not tax_id:
            untaxed += 1
            continue
        tax_row = taxes.get(tax_id)
        if tax_row is None:
            uncertain.append(f"tax {tax_id} is not in the pinned active tax list")
            continue
        if tax_row["tax_type"] != "tax":
            uncertain.append(
                f"tax {tax_row['tax_name']} ({tax_id}) is a {tax_row['tax_type']}, so its "
                "component rounding is not predictable here"
            )
        percentage = Decimal(tax_row["tax_percentage"])
        buckets[tax_id] = buckets.get(tax_id, zero) + net
        per_line_tax += (net * percentage / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    tax_rows = []
    bucket_tax = zero
    for tax_id in sorted(buckets):
        tax_row = taxes[tax_id]
        percentage = Decimal(tax_row["tax_percentage"])
        amount = (buckets[tax_id] * percentage / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        bucket_tax += amount
        tax_rows.append({
            "tax_id": tax_id,
            "tax_name": tax_row["tax_name"],
            "tax_percentage": tax_row["tax_percentage"],
            "taxable_net": money_text(buckets[tax_id]),
            "tax_amount": money_text(amount),
        })
    return {
        "sub_total": money_text(sub_total),
        "tax_rows": tax_rows,
        "tax_total": money_text(bucket_tax),
        "per_line_tax_total": money_text(per_line_tax),
        "tax_methods_agree": bucket_tax == per_line_tax,
        "total": money_text(sub_total + bucket_tax),
        "untaxed_line_count": untaxed,
        "tax_uncertainty_reasons": sorted(set(uncertain)),
    }


def settle_tax_certainty(
    action: str, before: dict[str, Any], record: dict[str, Any], after: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Corroborate the tax method against the record's OWN live tax_total.

    On a purely untaxed record there is nothing to settle. On the estimate the
    two methods disagree by a cent (per-bucket 2780.86 versus per-line 2780.85),
    so the method is not chosen by preference: whichever one reproduces Zoho's
    own current figure for THIS record is the corroborated one, and if neither
    does, nothing is asserted and the plan says so.
    """
    live_tax = live_decimal(record.get("tax_total", 0), "live tax_total")
    reasons = list(before["tax_uncertainty_reasons"])
    corroborated = False
    if action == ACTION_PURCHASE_ORDER:
        corroborated = (
            live_tax == 0 and Decimal(before["tax_total"]) == 0
            and Decimal(after["tax_total"]) == 0 and not reasons
        )
        if not corroborated:
            reasons.append(
                "this purchase order is expected to carry no tax at all, and the live figures do "
                "not agree"
            )
    else:
        if live_tax == Decimal(before["tax_total"]):
            corroborated = not reasons
        else:
            reasons.append(
                f"the per-bucket method reproduces {before['tax_total']} for the unchanged record "
                f"while Zoho's own live tax_total is {money_text(live_tax)}, so the method that "
                "predicts this record's tax is not established"
            )
        if not before["tax_methods_agree"]:
            reasons.append(
                f"per-bucket tax {before['tax_total']} and per-line tax "
                f"{before['per_line_tax_total']} differ on the unchanged record; the per-bucket "
                "figure is used because it is the one Zoho's own live tax_total matches"
            )
    corroboration = {
        "live_tax_total": money_text(live_tax),
        "recomputed_before_tax_total": before["tax_total"],
        "recomputed_before_per_line_tax_total": before["per_line_tax_total"],
        "method": "per_tax_bucket_half_up",
        "corroborated_against_live_record": corroborated,
        "notes": sorted(set(reasons)),
    }
    settled = dict(after)
    settled["tax_certainty"] = "corroborated_exact" if corroborated else "disclosed_uncertain"
    settled["tax_total_asserted"] = corroborated
    settled["tax_uncertainty_reasons"] = sorted(set(reasons))
    return settled, corroboration


# ---------------------------------------------------------------------------
# The one PUT body
# ---------------------------------------------------------------------------


def put_payload(action: str, record: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserved live header values, every original line, then the two additions.

    Zoho deletes existing lines that a PUT omits, so the COMPLETE original list
    is always resent in live order, each carrying its own line_item_id and
    item_id. There is no status key here and no key that could mail, convert or
    restatus anything.
    """
    target = TARGETS[require_action(action)]
    payload: dict[str, Any] = {}
    for key in PUT_HEADER_KEYS[action]:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value
    for key in PUT_STRING_CONTROL_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value
    for key in PUT_BOOL_CONTROL_KEYS:
        if isinstance(record.get(key), bool):
            payload[key] = record[key]
    for key in PUT_NUMBER_CONTROL_KEYS:
        if key in record:
            payload[key] = number_json(live_decimal(record.get(key), f"live {key}"))
    for key in PUT_LIST_CONTROL_KEYS:
        value = record.get(key)
        if isinstance(value, list) and value and all(
            ID_RE.fullmatch(str(entry or "")) for entry in value
        ):
            payload[key] = [str(entry) for entry in value]
    live_lines = record.get("line_items") or []
    put_lines: list[dict[str, Any]] = []
    original = [row for row in rows if row["origin"] == "original_preserved"]
    appended = [row for row in rows if row["origin"] == "appended_non_catalog"]
    if len(original) != target["line_count"] or len(appended) != 2:
        raise J26RevisionToolError("The projected line set is not the exact commissioned shape.")
    for index, (line, row) in enumerate(zip(live_lines, original)):
        if str(line.get("line_item_id") or "") != row["line_item_id"]:
            raise J26RevisionToolError("The projected line order does not match the live order.")
        put_line: dict[str, Any] = {
            "line_item_id": row["line_item_id"],
            "item_id": row["item_id"],
            "name": row["name"],
            "quantity": number_json(Decimal(row["quantity"])),
            "rate": number_json(Decimal(row["rate"])),
        }
        if row["description"]:
            put_line["description"] = row["description"]
        if row["unit"]:
            put_line["unit"] = row["unit"]
        if isinstance(row["item_order"], int) and not isinstance(row["item_order"], bool):
            put_line["item_order"] = row["item_order"]
        if row["tax_id"]:
            put_line["tax_id"] = row["tax_id"]
        if "discount" in line:
            discount = live_decimal(line.get("discount", 0), f"live line {index + 1} discount")
            if discount != 0:
                raise J26RevisionToolError(
                    f"Live line {index + 1} carries a nonzero discount this action cannot restate."
                )
            put_line["discount"] = 0
        unknown = sorted(set(put_line) - set(EXISTING_LINE_PUT_KEYS))
        if unknown or not REQUIRED_EXISTING_LINE_PUT_KEYS.issubset(put_line):
            raise J26RevisionToolError(
                f"Original line {index + 1} cannot be resent intact as the commissioned shape."
            )
        put_lines.append(put_line)
    for row in appended:
        new_line: dict[str, Any] = {
            "name": row["name"],
            "description": row["description"],
            "quantity": number_json(Decimal(row["quantity"])),
            "rate": number_json(Decimal(row["rate"])),
            "unit": row["unit"],
        }
        if row["tax_id"]:
            new_line["tax_id"] = row["tax_id"]
        present = sorted(set(new_line) & set(FORBIDDEN_NEW_LINE_KEYS))
        if present:
            raise J26RevisionToolError(
                "An appended line names " + ", ".join(present)
                + ", which a non-catalog free-text line must never carry."
            )
        unknown = sorted(set(new_line) - set(NEW_LINE_PUT_KEYS[action]))
        if unknown or not REQUIRED_NEW_LINE_PUT_KEYS.issubset(new_line):
            raise J26RevisionToolError("An appended line is not the exact commissioned shape.")
        put_lines.append(new_line)
    payload["line_items"] = put_lines
    extra = sorted(set(payload) - ALLOWED_PUT_KEYS[action])
    if extra or not REQUIRED_PUT_KEYS[action].issubset(payload):
        raise J26RevisionToolError("The revision payload is not the exact commissioned shape.")
    return payload


def protected_state(action: str, record: dict[str, Any]) -> dict[str, Any]:
    """Everything Zoho returns EXCEPT what appending two lines legitimately moves.

    The header money figures and the line list are recomputed by Zoho, so each is
    asserted by its own explicit rule after the PUT. The ORIGINAL lines are
    fingerprinted here field by field, so a change to one of them cannot hide.
    """
    target = TARGETS[require_action(action)]
    result = {
        key: value for key, value in record.items()
        if key not in HEADER_DERIVED_KEYS and key not in VOLATILE_KEYS
    }
    result["header_taxes_protected"] = [
        {key: value for key, value in tax.items() if key not in TAX_AMOUNT_KEYS}
        for tax in (record.get("taxes") or []) if isinstance(tax, dict)
    ]
    original: list[dict[str, Any]] = []
    for line in (record.get("line_items") or [])[: target["line_count"]]:
        if not isinstance(line, dict):
            continue
        protected_line = {
            key: value for key, value in line.items() if key not in LINE_DERIVED_KEYS
        }
        protected_line["line_item_taxes_protected"] = [
            {key: value for key, value in tax.items() if key not in TAX_AMOUNT_KEYS}
            for tax in (line.get("line_item_taxes") or []) if isinstance(tax, dict)
        ]
        original.append(protected_line)
    result["original_lines_protected"] = original
    return result


def build_revision(
    action: str,
    record: dict[str, Any],
    intent: dict[str, Any],
    taxes: dict[str, dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """The whole projection, derived from immutable inputs alone.

    Commit re-runs this over the staged intent and FRESH live evidence and
    refuses unless the result is byte-identical to the reviewed plan, so no
    figure, endpoint, payload key or fingerprint in a plan file can be tampered
    with on its own.
    """
    target = TARGETS[require_action(action)]
    require_fixed_source_evidence(sources)
    links = validate_live_record(action, record)
    lines = live_original_lines(action, record)
    if action == ACTION_ESTIMATE:
        if sorted(taxes) != [ON_HST_TAX_ID]:
            raise J26RevisionToolError(
                "Plan tax evidence is not exactly the estimate's own ON HST row."
            )
        if Decimal(taxes[ON_HST_TAX_ID]["tax_percentage"]) != ON_HST_PERCENTAGE:
            raise J26RevisionToolError("Plan tax evidence is not the reviewed 13% ON HST row.")
    elif taxes:
        raise J26RevisionToolError(
            "A purchase-order revision prices no tax at all; plan tax evidence must be empty."
        )
    before_rows = original_rows(action, lines)
    new_rows = appended_rows(action, intent, len(before_rows))
    after_rows = before_rows + new_rows
    before_totals = totals_for(before_rows, taxes)
    after_totals_raw = totals_for(after_rows, taxes)
    after_totals, corroboration = settle_tax_certainty(
        action, before_totals, record, after_totals_raw
    )
    protected = protected_state(action, record)
    return {
        "tool_version": TOOL_VERSION,
        "action": action,
        "record": {
            "type": target["record"],
            "record_id": target["record_id"],
            "record_number": target["record_number"],
            "reference_number": target["reference_number"],
            "party_key": target["party_key"],
            "party_id": target["party_id"],
            "party_name": target["party_name"],
            "currency_code": target["currency_code"],
            "currency_id": str(record.get("currency_id") or ""),
            "exchange_rate": format(
                live_decimal(record.get("exchange_rate", 1), "live exchange_rate"), "f"
            ),
            "status": target["status"],
            "is_emailed": bool(record.get("is_emailed")) if "is_emailed" in record else None,
            "original_line_count": target["line_count"],
            "final_line_count": target["line_count"] + 2,
            "downstream_links_observed": links,
            "before_state": json_copy(record),
            "before_state_sha256": digest_for(record),
            "protected_state": protected,
            "protected_state_sha256": digest_for(protected),
        },
        "sources": sources,
        "lug_cost_evidence": json_copy(intent["lifting_lug_supplier_unit_cost_usd"]),
        "operator_source_note": intent["operator_source_note"],
        "original_lines": before_rows,
        "appended_lines": new_rows,
        "all_lines_after": after_rows,
        "tax_rows_used": {tax_id: json_copy(taxes[tax_id]) for tax_id in sorted(taxes)},
        "tax_corroboration": corroboration,
        "current_totals": before_totals,
        "expected_totals": after_totals,
        "disclosures": [json_copy(entry) for entry in DISCLOSURES],
        "contract": contract_status(action),
        "put_endpoint": f"PUT {record_path(action, target['record_id'])}",
        "put_payload": put_payload(action, record, after_rows),
        "status_unchanged": target["status"],
        "creates_zoho_item": False,
        "email_sent": False,
        "other_record_untouched": (
            ESTIMATE_TARGET["record_number"] if action == ACTION_PURCHASE_ORDER
            else PURCHASE_ORDER_TARGET["record_number"]
        ),
    }


def stable_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    """Canonical reviewed projection with per-GET telemetry normalized out.

    Full raw GET evidence stays immutable in the saved plan. For the fresh
    pre-lock comparison the before-state copy and its digest are replaced by the
    same stable business state the explicit drift gate uses, so a regenerated
    secure record URL cannot make two otherwise equal projections differ. Every
    business field, projected value, total, PUT key and fingerprint stays exact.
    """
    stable = json_copy(evidence)
    record = stable.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("before_state"), dict):
        raise J26RevisionToolError("Revision evidence has no stable before-state projection.")
    before = prewrite_state(record["before_state"])
    record["before_state"] = before
    record["before_state_sha256"] = digest_for(before)
    return stable


def prewrite_state(record: dict[str, Any]) -> dict[str, Any]:
    """The live record minus only the keys Zoho regenerates between GETs."""
    return {key: value for key, value in record.items() if key not in VOLATILE_KEYS}


# ---------------------------------------------------------------------------
# Plan staging
# ---------------------------------------------------------------------------


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise J26RevisionToolError("Plan must be an absolute path inside the exact plan folder.")
    lexical_root = PLAN_DIR.absolute()
    try:
        candidate.absolute().relative_to(lexical_root)
    except ValueError as exc:
        raise J26RevisionToolError("Plan is outside the exact allowlisted plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise J26RevisionToolError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise J26RevisionToolError("Plan is outside the exact allowlisted plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise J26RevisionToolError("Plan does not resolve inside the exact plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise J26RevisionToolError("Plan is outside the exact allowlisted plan folder.")
    return resolved


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise J26RevisionToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / LOCK_DIRNAME / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    """Durable single-use lock. Created BEFORE the PUT, never removed after."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # A4: what the existing record says decides -- spent, or needs re-stage.
        owner_authority.refuse_replay(J26RevisionToolError, owner_authority.read_json_if_exists(path),
                                      what="J26-403 revision plan")
        raise  # unreachable
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def approval_binding(created: datetime, expires: datetime) -> dict[str, Any]:
    """One APPROVED answers ONE plan, and only a word sent AFTER it exists."""
    return {
        "approval_required": APPROVAL_WORD,
        "answers_exactly_one_plan": True,
        "plan_created_utc": created.isoformat(),
        "plan_expires_utc": expires.isoformat(),
        "caller_must_compare_message_time": True,
        "rule": (
            "The approval word must come from a Rachad message SENT AFTER this plan's "
            "plan_created_utc and BEFORE plan_expires_utc. A word sent before the plan existed "
            "cannot approve it, and a word answering the OTHER action's plan cannot approve this "
            "one. The relaying workflow must compare the message timestamp against "
            "plan_created_utc before passing the word to commit; commit requires "
            "--approval-message-utc and re-checks it."
        ),
    }


def stage_plan(
    action: str, intent: dict[str, Any], evidence: dict[str, Any], organization_id: str,
    input_path: Path,
) -> Path:
    target = TARGETS[require_action(action)]
    created = utc_now()
    expires = created + timedelta(hours=PLAN_LIFETIME_HOURS)
    core = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "kind": PLAN_KINDS[action],
        "action": action,
        "schema_version": SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": expires.isoformat(),
        "approval_required": APPROVAL_WORD,
        "approval_binding": approval_binding(created, expires),
        "record_id": target["record_id"],
        "record_number": target["record_number"],
        "books_organization_id": organization_id,
        "risk": {
            "atomic": True,
            "single_put": True,
            "reversible": False,
            "email_sent": False,
            "write_attempted": False,
            "status_unchanged": target["status"],
            "creates_zoho_item": False,
            "one_record_only": True,
            "note": RISK_NOTE,
        },
        "input": {"path": str(input_path), "sha256": file_digest(input_path)},
        "intent": intent,
        "live_evidence": evidence,
    }
    plan = json_copy(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{PLAN_KINDS[action]}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(
        f"zoho_j26_403_{action}_plan_staged_read_only",
        f"record={target['record_number']} ({target['record_id']}); "
        f"appended_lines={len(evidence['appended_lines'])}; "
        f"total={evidence['expected_totals']['total']} {target['currency_code']}; "
        f"commit_blocked={str(evidence['contract']['commit_blocked']).lower()}; "
        f"plan={path}; sha256={plan['sha256']}; writes=0; method=GET_ONLY",
    )
    return path


def print_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    record = evidence["record"]
    current = evidence["current_totals"]
    expected = evidence["expected_totals"]
    currency = record["currency_code"]
    print("=" * 78)
    print(f"STAGED PLAN -- {plan['action'].upper()} (NOT APPROVED, NOTHING WRITTEN)")
    print("=" * 78)
    print(f"Record        : {record['record_number']} ({record['record_id']}) "
          f"-- {record['type']}, status {record['status']}")
    print(f"Party         : {record['party_name']} ({record['party_id']})")
    print(f"Reference     : {record['reference_number']}")
    print(f"Currency      : {currency} (live value, preserved and never set)")
    print(f"Endpoint      : {evidence['put_endpoint']} (ONE PUT, no retry)")
    print(f"Other record  : {evidence['other_record_untouched']} is NOT touched by this plan "
          "and needs its own plan and its own approval.")
    print("-" * 78)
    print(f"{'#':>2}  {'Line':46} {'Qty':>6} {'Rate':>12} {'Total':>12}")
    for row in evidence["all_lines_after"]:
        marker = "+" if row["origin"] == "appended_non_catalog" else " "
        print(f"{row['position']:>2}{marker} {row['name'][:45]:45} {row['quantity']:>6} "
              f"{row['rate']:>12} {row['item_total']:>12}")
    print("    (+ marks an APPENDED non-catalog line: no item_id, no SKU, no Zoho item created)")
    print("-" * 78)
    for row in evidence["appended_lines"]:
        pricing = row["pricing"]
        print(f"{row['key']}: supplier USD {pricing['supplier_unit_cost_usd']} "
              f"x {pricing['multiplier'] if pricing['multiplier_applied'] else '1 (no multiplier)'}"
              f" = {pricing['unrounded']} -> posted {pricing['posted_rate']} "
              f"({pricing['rounding']})")
        print(f"    source: {pricing['source']}")
        print(f"    artifact: {pricing['artifact_name']} sha256 {pricing['artifact_sha256']}")
    print("-" * 78)
    print(f"Sub total   before {currency} {current['sub_total']:>12}   after {currency} "
          f"{expected['sub_total']:>12}")
    print(f"Tax total   before {currency} {current['tax_total']:>12}   after {currency} "
          f"{expected['tax_total']:>12}")
    print(f"Grand total before {currency} {current['total']:>12}   after {currency} "
          f"{expected['total']:>12}")
    print(f"Tax prediction: {expected['tax_certainty']} "
          f"(method {evidence['tax_corroboration']['method']}, corroborated="
          f"{evidence['tax_corroboration']['corroborated_against_live_record']})")
    for note in evidence["tax_corroboration"]["notes"]:
        print(f"  NOTE {note}")
    print("-" * 78)
    print("DISCLOSED -- what the sources do NOT establish:")
    for entry in evidence["disclosures"]:
        print(f"  [{entry['key']}]")
        print(f"    {entry['statement']}")
    print("-" * 78)
    contract = evidence["contract"]
    print(f"ZOHO CONTRACT PROOF: all_proven={contract['all_proven']} "
          f"commit_blocked={contract['commit_blocked']}")
    for fact in contract["facts"]:
        state = "PROVEN" if fact["proven"] else "NOT PROVEN"
        print(f"  [{state}] {fact['key']}: {fact['statement']}")
        if not fact["proven"]:
            print(f"      why: {fact['why_unproven']}")
            print(f"      proof needed: {fact['what_would_prove_it']}")
    if contract["commit_blocked"]:
        print("  *** COMMIT WILL REFUSE this plan before any lock, token or network call. ***")
    print("-" * 78)
    print(f"NOT REVERSIBLE : {plan['risk']['note']}")
    print(f"Email sent     : NO -- this tool has no mail transport")
    print(f"Zoho item made : NO -- both additions are free-text lines")
    print(f"Plan           : {path}")
    print(f"Plan sha256    : {plan['sha256']}")
    print(f"Created        : {plan['created_utc']}")
    print(f"Expires        : {plan['expires_utc']}")
    print("-" * 78)
    print(
        f"NO WRITE HAS BEEN MADE. Committing this plan needs Rachad's own one-word reply\n"
        f"{APPROVAL_WORD} to THIS plan (exact uppercase), sent AFTER {plan['created_utc']}.\n"
        f"One {APPROVAL_WORD} answers ONE plan; the other record needs its own."
    )
    print("=" * 78)


def collect_live_evidence(
    access_token: str, vault: dict[str, Any], action: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Every read the projection needs, GET-only and route-allowlisted."""
    record = get_record(access_token, vault, action)
    taxes: dict[str, dict[str, Any]] = {}
    if action == ACTION_ESTIMATE:
        taxes = on_hst_evidence(get_active_taxes(access_token, vault))
    return record, taxes


def command_stage(args: argparse.Namespace, action: str) -> None:
    """GET-only. Refuses before any read if the narrow update scope is not live."""
    require_action(action)
    input_path = Path(str(args.input))
    intent = lug_cost_intent(read_json(str(input_path)))
    sources = fixed_source_evidence()
    vault = zoho_tool.load_vault()
    require_update_scopes(action, [str(scope) for scope in vault.get("scopes") or []])
    organization_id = books_organization_id(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    record, taxes = collect_live_evidence(access_token, vault, action)
    zoho_tool.save_vault(vault)
    evidence = build_revision(action, record, intent, taxes, sources)
    path = stage_plan(action, intent, evidence, organization_id, input_path)
    print_summary(read_json(str(path)), path)


def command_stage_purchase_order_revision(args: argparse.Namespace) -> None:
    command_stage(args, ACTION_PURCHASE_ORDER)


def command_stage_estimate_revision(args: argparse.Namespace) -> None:
    command_stage(args, ACTION_ESTIMATE)


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_plan(plan: dict[str, Any], action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    require_action(action)
    target = TARGETS[action]
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise J26RevisionToolError(
            "The plan belongs to a different tool, build or schema version."
        )
    # One command can only ever commit its own action's plan. This is what makes
    # a single APPROVED unable to move both records.
    if plan.get("action") != action or plan.get("kind") != PLAN_KINDS[action]:
        raise J26RevisionToolError(
            f"REFUSED: this plan is for {plan.get('action')!r}, not {action!r}. Each action has "
            "its own plan and its own approval; one plan can never carry both records and one "
            "approval can never answer two plans."
        )
    if str(plan.get("record_id") or "") != target["record_id"]:
        raise J26RevisionToolError("Plan names a different record than this action's fixed one.")
    if str(plan.get("record_number") or "") != target["record_number"]:
        raise J26RevisionToolError("Plan names a different record number.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise J26RevisionToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise J26RevisionToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise J26RevisionToolError("Plan creation time is in the future.")
    if now >= expires:
        raise J26RevisionToolError("Plan expired. Stage a new plan for review.")
    if plan.get("approval_binding") != approval_binding(created, expires):
        raise J26RevisionToolError(
            "Plan must carry the exact one-approval-answers-one-plan binding."
        )
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_put") is not True
        or risk.get("reversible") is not False
        or risk.get("email_sent") is not False
        or risk.get("write_attempted") is not False
        or risk.get("creates_zoho_item") is not False
        or risk.get("one_record_only") is not True
        or risk.get("status_unchanged") != target["status"]
        or risk.get("note") != RISK_NOTE
    ):
        raise J26RevisionToolError(
            "Plan must disclose the exact single-atomic-PUT, one-record, not-reversible risk."
        )
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise J26RevisionToolError("Plan organization ID is invalid.")
    # Re-normalizing through the same closed schema means a hand-edited intent
    # cannot smuggle in a price with no artifact, a generic source or an extra
    # field, and it re-hashes the artifact on disk while doing it.
    intent = lug_cost_intent(plan_intent_input(plan.get("intent")))
    if intent != plan.get("intent"):
        raise J26RevisionToolError(
            "Plan intent is not the canonical normalized form of its own input."
        )
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise J26RevisionToolError("Plan evidence is invalid.")
    if evidence.get("tool_version") != TOOL_VERSION:
        raise J26RevisionToolError("Plan evidence was produced by a different build.")
    if evidence.get("action") != action:
        raise J26RevisionToolError("Plan evidence is for a different action.")
    record_evidence = evidence.get("record")
    if not isinstance(record_evidence, dict):
        raise J26RevisionToolError("Plan record evidence is invalid.")
    before = record_evidence.get("before_state")
    if not isinstance(before, dict):
        raise J26RevisionToolError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(
        str(record_evidence.get("before_state_sha256") or ""), digest_for(before)
    ):
        raise J26RevisionToolError("Plan before-state evidence hash is invalid.")
    taxes = evidence.get("tax_rows_used")
    if not isinstance(taxes, dict):
        raise J26RevisionToolError("Plan tax evidence is invalid.")
    sources = evidence.get("sources")
    require_fixed_source_evidence(sources)
    # Re-derive EVERYTHING from the staged live state, intent, tax rows and the
    # re-hashed sources. A tampered payload, endpoint, fingerprint, disclosure,
    # contract claim or total cannot survive this.
    rebuilt = build_revision(action, before, intent, taxes, sources)
    if rebuilt != evidence:
        raise J26RevisionToolError(
            "Plan evidence is not the canonical projection of the staged live state."
        )
    if evidence["put_endpoint"] != f"PUT {record_path(action, target['record_id'])}":
        raise J26RevisionToolError("Plan endpoint is not the one commissioned route.")
    return intent, evidence


def plan_intent_input(intent: Any) -> Any:
    """Turn a stored normalized intent back into raw input shape for re-validation."""
    if not isinstance(intent, dict):
        raise J26RevisionToolError("Plan intent is invalid.")
    unknown = sorted(set(intent) - INPUT_KEYS)
    if unknown:
        raise J26RevisionToolError(
            "Plan intent names uncommissioned field(s): " + ", ".join(unknown)
        )
    entry = intent.get("lifting_lug_supplier_unit_cost_usd")
    if not isinstance(entry, dict):
        raise J26RevisionToolError("Plan intent carries no lifting-lug cost evidence.")
    raw: dict[str, Any] = {
        "lifting_lug_supplier_unit_cost_usd": {
            "value": entry.get("value"),
            "source": entry.get("source"),
            "artifact_path": entry.get("artifact_path"),
            "artifact_sha256": entry.get("artifact_sha256"),
        }
    }
    if intent.get("operator_source_note"):
        raw["operator_source_note"] = intent["operator_source_note"]
    return raw


def load_plan(path: Path, action: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    if not isinstance(plan, dict):
        raise J26RevisionToolError("Plan must contain one object.")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise J26RevisionToolError("Plan hash check failed. The plan changed after review.")
    intent, evidence = validate_plan(plan, action)
    return plan, intent, evidence


def require_approval_message_time(plan: dict[str, Any], stated: Any) -> str:
    """Caller-side proof that the word came AFTER this plan existed.

    The shared owner-authority check already refuses a missing time before
    this runs (2026-08-21); the empty return is kept only for that ordering.
    """
    if stated is None:
        return ""
    moment = parse_plan_time(stated, "approval message time")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if moment < created:
        raise J26RevisionToolError(
            f"REFUSED: the approval message is timestamped {moment.isoformat()}, BEFORE this plan "
            f"was created at {created.isoformat()}. A word sent before the plan existed cannot "
            "approve it. No Zoho call was made."
        )
    if moment >= expires:
        raise J26RevisionToolError(
            f"REFUSED: the approval message is timestamped {moment.isoformat()}, at or after this "
            f"plan expired at {expires.isoformat()}. No Zoho call was made."
        )
    return moment.isoformat()


# ---------------------------------------------------------------------------
# The one write path
# ---------------------------------------------------------------------------


def require_put_allowed(
    action: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    """The complete write allowlist. Pure validation -- it touches nothing.

    Only PUT. Only the record this reviewed plan names. Only the commissioned
    keys, with every original line resent in reviewed order carrying its own
    line_item_id and item_id, exactly two appended non-catalog lines carrying
    neither, and every value identical to the reviewed plan. Commit runs it once
    BEFORE the replay lock (so a bad payload is a free refusal, not a burned
    plan) and the write function runs it again as the transport's own gate.
    """
    require_action(action)
    target = TARGETS[action]
    record = evidence["record"]
    if method != "PUT":
        raise J26RevisionToolError(
            "REFUSED: this revision is a PUT and nothing else. There is no POST, PATCH or DELETE "
            "verb in this tool."
        )
    pattern = (
        PURCHASE_ORDER_PUT_PATH_RE if action == ACTION_PURCHASE_ORDER else ESTIMATE_PUT_PATH_RE
    )
    match = pattern.fullmatch(str(path))
    if not match or match.group(1) != record["record_id"] or record["record_id"] != target["record_id"]:
        raise J26RevisionToolError(
            "REFUSED: this action targets exactly "
            f"{record_path(action, target['record_id'])} and nothing else. Creation, deletion, "
            "cloning, sending, mailing, approval, acceptance, decline, conversion, receiving, "
            "billing, payment, attachment, template, status and every bulk route are unreachable, "
            "and the other J26-403 record is unreachable from this command."
        )
    if not isinstance(payload, dict) or not isinstance(expected_payload, dict):
        raise J26RevisionToolError("REFUSED: the PUT payload must be an object.")
    extra = sorted(set(payload) - ALLOWED_PUT_KEYS[action])
    if extra:
        raise J26RevisionToolError(
            "REFUSED: the PUT payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not REQUIRED_PUT_KEYS[action].issubset(payload):
        raise J26RevisionToolError(
            "REFUSED: the PUT payload must carry the preserved party, number and date plus the "
            "complete line list."
        )
    if "status" in payload:
        raise J26RevisionToolError("REFUSED: no status field is ever sent by this tool.")
    if str(payload.get(target["party_key"]) or "") != target["party_id"]:
        raise J26RevisionToolError("REFUSED: the PUT names a different party.")
    if str(payload.get(target["number_key"]) or "") != target["record_number"]:
        raise J26RevisionToolError("REFUSED: the PUT does not preserve the record number.")
    if str(payload.get("reference_number") or "") != target["reference_number"]:
        raise J26RevisionToolError("REFUSED: the PUT does not preserve the reference number.")
    lines = payload.get("line_items")
    expected_lines = expected_payload.get("line_items")
    final_count = record["final_line_count"]
    if not isinstance(lines, list) or len(lines) != final_count:
        raise J26RevisionToolError(
            f"REFUSED: the PUT must carry exactly {final_count} lines -- every original line plus "
            "the two additions."
        )
    if not isinstance(expected_lines, list) or len(expected_lines) != final_count:
        raise J26RevisionToolError("REFUSED: the reviewed plan payload is not the commissioned shape.")
    reviewed_rows = evidence["all_lines_after"]
    if len(reviewed_rows) != final_count:
        raise J26RevisionToolError("REFUSED: the reviewed plan does not project every line.")
    seen: set[str] = set()
    appended_seen = 0
    for index, (line, reviewed, row) in enumerate(zip(lines, expected_lines, reviewed_rows)):
        if not isinstance(line, dict) or not isinstance(reviewed, dict):
            raise J26RevisionToolError("REFUSED: every PUT line must be an object.")
        if row["origin"] == "original_preserved":
            unknown = sorted(set(line) - set(EXISTING_LINE_PUT_KEYS))
            if unknown:
                raise J26RevisionToolError(
                    "REFUSED: an original PUT line names uncommissioned field(s): "
                    + ", ".join(unknown)
                )
            if not REQUIRED_EXISTING_LINE_PUT_KEYS.issubset(line):
                raise J26RevisionToolError(
                    "REFUSED: every original PUT line must resend its own identity."
                )
            line_item_id = str(line.get("line_item_id") or "")
            if not ID_RE.fullmatch(line_item_id) or line_item_id in seen:
                raise J26RevisionToolError("REFUSED: the PUT repeats or omits a line_item_id.")
            seen.add(line_item_id)
            if line_item_id != row["line_item_id"]:
                raise J26RevisionToolError(
                    f"REFUSED: PUT line {index + 1} is not the reviewed line in the reviewed order."
                )
            if str(line.get("item_id") or "") != row["item_id"] or not ID_RE.fullmatch(row["item_id"]):
                raise J26RevisionToolError(
                    f"REFUSED: PUT line {index + 1} does not resend the reviewed Zoho item_id."
                )
        else:
            appended_seen += 1
            # The forbidden-key check runs BEFORE the unknown-key check so that
            # an item_id or SKU on an appended line gets the refusal that says
            # why, not the generic "uncommissioned field" one.
            present = sorted(set(line) & set(FORBIDDEN_NEW_LINE_KEYS))
            if present:
                raise J26RevisionToolError(
                    "REFUSED: an appended PUT line names " + ", ".join(present)
                    + ". The two additions are non-catalog free-text lines: they carry no item_id, "
                    "no line_item_id, no SKU and no stock or Inventory field, and this tool never "
                    "creates a Zoho item as a workaround."
                )
            unknown = sorted(set(line) - set(NEW_LINE_PUT_KEYS[action]))
            if unknown:
                raise J26RevisionToolError(
                    "REFUSED: an appended PUT line names uncommissioned field(s): "
                    + ", ".join(unknown)
                )
            if not REQUIRED_NEW_LINE_PUT_KEYS.issubset(line):
                raise J26RevisionToolError(
                    "REFUSED: every appended PUT line must carry its name, description, quantity, "
                    "rate and unit."
                )
            if line.get("name") not in (DIP_TUBE_NAME, LIFTING_LUG_NAME):
                raise J26RevisionToolError(
                    "REFUSED: an appended PUT line is not one of the two fixed additions."
                )
        if set(line) != set(reviewed):
            raise J26RevisionToolError(
                f"REFUSED: PUT line {index + 1} does not carry the reviewed field set."
            )
        for key in line:
            if line[key] != reviewed[key]:
                raise J26RevisionToolError(
                    f"REFUSED: PUT line {index + 1} {key} does not match the reviewed plan."
                )
    if appended_seen != 2:
        raise J26RevisionToolError(
            f"REFUSED: the PUT carries {appended_seen} appended line(s), not exactly the two fixed "
            "additions."
        )
    for key in sorted(ALLOWED_PUT_KEYS[action]):
        if key == "line_items":
            continue
        if payload.get(key) != expected_payload.get(key):
            raise J26RevisionToolError(
                f"REFUSED: the PUT {key} does not match the reviewed plan."
            )
    if set(payload) != set(expected_payload):
        raise J26RevisionToolError("REFUSED: the PUT is not the reviewed payload.")
    if not ID_RE.fullmatch(str(organization_id)):
        raise J26RevisionToolError("REFUSED: the organization ID is invalid.")


def send_put(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
    evidence: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    """The ONE write transport in this module. One attempt, no retry.

    The query string is exactly the organization id: there is no send, email,
    status, approve, convert or ignore-auto-number parameter here or anywhere
    else in this tool.
    """
    require_put_allowed(
        action, method, path, organization_id, payload, expected_payload, evidence
    )
    request = Request(
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise J26RevisionToolError(
            f"Zoho {action} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise J26RevisionToolError(
            f"Zoho {action} outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise J26RevisionToolError(
            f"Zoho {action} returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise J26RevisionToolError(
            f"Zoho {action} returned an invalid or unknown result: " + str(message)
        )
    return result


def verify_result(
    action: str, after: Any, evidence: dict[str, Any], label: str, *, full: bool = True
) -> None:
    """Post-state verification, applied to the PUT response AND a fresh GET.

    `full` adds the byte-exact protected fingerprint. It runs against the FRESH
    GET, which is the authoritative record.
    """
    require_action(action)
    target = TARGETS[action]
    record = evidence["record"]
    expected = evidence["expected_totals"]
    if not isinstance(after, dict):
        raise J26RevisionToolError(f"{label} returned no {target['record']} record.")
    for key, want in (
        (target["id_key"], record["record_id"]),
        (target["number_key"], record["record_number"]),
        ("reference_number", record["reference_number"]),
        (target["party_key"], record["party_id"]),
        ("currency_code", record["currency_code"]),
        ("currency_id", record["currency_id"]),
        ("status", record["status"]),
    ):
        actual = str(after.get(key) if after.get(key) is not None else "")
        if actual != want:
            raise J26RevisionToolError(
                f"{label} {key} is {actual!r}, not the preserved {want!r}. Stop and reconcile."
            )
    if record["is_emailed"] is not None and "is_emailed" in after:
        if bool(after.get("is_emailed")) is not record["is_emailed"]:
            raise J26RevisionToolError(
                f"{label} reports is_emailed {after.get('is_emailed')!r}, not the preserved "
                f"{record['is_emailed']!r}. Stop and reconcile: this tool has no mail transport."
            )
    # A conversion, package, shipment, bill, receipt or payment appearing across
    # the write is a stop, not a warning.
    require_no_disqualifying_state(action, after)
    lines = after.get("line_items")
    final_count = record["final_line_count"]
    if not isinstance(lines, list) or len(lines) != final_count:
        raise J26RevisionToolError(
            f"{label} carries {len(lines) if isinstance(lines, list) else 'no'} lines, not the "
            f"approved {final_count}. Stop and reconcile."
        )
    reviewed_rows = evidence["all_lines_after"]
    names_seen: dict[str, int] = {}
    for index, (line, want) in enumerate(zip(lines, reviewed_rows)):
        if not isinstance(line, dict):
            raise J26RevisionToolError(f"{label} line {index + 1} is not an object.")
        name = str(line.get("name") or "")
        names_seen[name] = names_seen.get(name, 0) + 1
        if want["origin"] == "original_preserved":
            for key, expected_value in (
                ("line_item_id", want["line_item_id"]),
                ("item_id", want["item_id"]),
                ("name", want["name"]),
            ):
                if str(line.get(key) or "") != expected_value:
                    raise J26RevisionToolError(
                        f"{label} line {index + 1} {key} is {line.get(key)!r}, not the preserved "
                        f"{expected_value!r}. Stop and reconcile."
                    )
            if line.get("item_order") != want["item_order"]:
                raise J26RevisionToolError(
                    f"{label} line {index + 1} item_order moved from {want['item_order']!r} to "
                    f"{line.get('item_order')!r}. Stop and reconcile."
                )
        else:
            if str(line.get("name") or "") != want["name"]:
                raise J26RevisionToolError(
                    f"{label} line {index + 1} is {line.get('name')!r}, not the approved appended "
                    f"line {want['name']!r}. Stop and reconcile."
                )
            if str(line.get("item_id") or "").strip():
                raise J26RevisionToolError(
                    f"{label} line {index + 1} came back linked to Zoho item "
                    f"{line.get('item_id')!r}. The appended lines are non-catalog free-text lines "
                    "and must create or link NO item. Stop and reconcile."
                )
            if str(line.get("sku") or "").strip():
                raise J26RevisionToolError(
                    f"{label} line {index + 1} came back carrying SKU {line.get('sku')!r}. Stop "
                    "and reconcile."
                )
            if not ID_RE.fullmatch(str(line.get("line_item_id") or "")):
                raise J26RevisionToolError(
                    f"{label} line {index + 1} has no Zoho-assigned line_item_id. Stop and "
                    "reconcile."
                )
            if str(line.get("line_item_id") or "") in {
                row["line_item_id"] for row in reviewed_rows
                if row["origin"] == "original_preserved"
            }:
                raise J26RevisionToolError(
                    f"{label} line {index + 1} reuses an original line_item_id. Stop and reconcile."
                )
        for key in ("quantity", "rate"):
            actual_value = live_decimal(line.get(key), f"{label} line {index + 1} {key}")
            if actual_value != Decimal(want[key]):
                raise J26RevisionToolError(
                    f"{label} line {index + 1} {key} is {actual_value}, not the approved "
                    f"{want[key]}. Stop and reconcile."
                )
        if want["tax_id"] and str(line.get("tax_id") or "") != want["tax_id"]:
            raise J26RevisionToolError(
                f"{label} line {index + 1} tax_id is {line.get('tax_id')!r}, not the approved "
                f"{want['tax_id']!r}. Stop and reconcile."
            )
        if not want["tax_id"] and str(line.get("tax_id") or "").strip():
            raise J26RevisionToolError(
                f"{label} line {index + 1} came back taxed at {line.get('tax_id')!r} when the "
                "approved line carries no tax. Stop and reconcile."
            )
        if want["description"] and str(line.get("description") or "") != want["description"]:
            raise J26RevisionToolError(
                f"{label} line {index + 1} description is not the approved text. Zoho may have "
                "truncated or ignored it. Stop and reconcile."
            )
        if "item_total" in line:
            actual_total = live_decimal(line.get("item_total"), f"{label} line {index + 1} total")
            if actual_total != Decimal(want["item_total"]):
                raise J26RevisionToolError(
                    f"{label} line {index + 1} total is {actual_total}, not the approved "
                    f"{want['item_total']}. Stop and reconcile."
                )
    for row in evidence["appended_lines"]:
        if names_seen.get(row["name"], 0) != 1:
            raise J26RevisionToolError(
                f"{label} carries the appended line {row['name']!r} "
                f"{names_seen.get(row['name'], 0)} time(s), not exactly once. Stop and reconcile."
            )
    sub_total = live_decimal(after.get("sub_total"), f"{label} sub_total")
    if sub_total != Decimal(expected["sub_total"]):
        raise J26RevisionToolError(
            f"{label} sub_total is {sub_total}, not the approved {expected['sub_total']}. Stop and "
            "reconcile."
        )
    tax_total = live_decimal(after.get("tax_total", 0), f"{label} tax_total")
    total = live_decimal(after.get("total"), f"{label} total")
    if expected["tax_total_asserted"]:
        if tax_total != Decimal(expected["tax_total"]):
            raise J26RevisionToolError(
                f"{label} tax_total is {tax_total}, not the approved {expected['tax_total']}. Stop "
                "and reconcile."
            )
        if total != Decimal(expected["total"]):
            raise J26RevisionToolError(
                f"{label} total is {total}, not the approved {expected['total']}. Stop and "
                "reconcile."
            )
    if total != sub_total + tax_total:
        raise J26RevisionToolError(
            f"{label} total {total} is not its own sub_total plus tax_total. Stop and reconcile."
        )
    tax_rows = after.get("taxes")
    if isinstance(tax_rows, list) and tax_rows:
        rows_total = Decimal("0")
        for row in tax_rows:
            if not isinstance(row, dict):
                raise J26RevisionToolError(f"{label} returned an invalid tax row.")
            rows_total += live_decimal(row.get("tax_amount"), f"{label} tax row amount")
        if rows_total != tax_total:
            raise J26RevisionToolError(
                f"{label} tax rows sum to {rows_total}, not its own tax_total {tax_total}. Stop "
                "and reconcile."
            )
    if not full:
        return
    protected = protected_state(action, after)
    if protected != record["protected_state"] or not secrets.compare_digest(
        digest_for(protected), str(record["protected_state_sha256"])
    ):
        moved = sorted(
            key for key in set(protected) | set(record["protected_state"])
            if protected.get(key) != record["protected_state"].get(key)
        )
        raise J26RevisionToolError(
            f"{label} changed protected field(s) that must not move: {', '.join(moved) or 'unknown'}. "
            "Stop and reconcile."
        )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def command_commit(args: argparse.Namespace, action: str) -> None:
    require_action(action)
    target = TARGETS[action]
    plan_path = contained_plan(args.plan)
    plan, intent, evidence = load_plan(plan_path, action)
    # His go is checked before the contract gate, the lock, the vault, the
    # token and the network; the shared check also enforces "sent after the
    # plan was written" (--approval-message-utc is required).
    go = require_exact_approval(
        args.approval, plan, lane=getattr(args, "approval_lane", None),
        sent_utc=getattr(args, "approval_message_utc", None),
    )
    approval_message_utc = require_approval_message_time(
        plan, getattr(args, "approval_message_utc", None)
    )
    label = f"{target['record_number']} ({target['record_id']})"
    lock = lock_path(plan["sha256"])
    if lock.exists():
        owner_authority.refuse_replay(J26RevisionToolError, owner_authority.read_json_if_exists(lock),
                                      what="J26-403 revision plan")
    try:
        # The contract gate is FIRST inside the pre-lock section: no vault is
        # opened, no token is read and no packet leaves this machine while
        # Zoho's contract for this action is unproven.
        require_proven_contract(action)
        vault = zoho_tool.load_vault()
        require_update_scopes(action, [str(scope) for scope in vault.get("scopes") or []])
        organization_id = books_organization_id(vault)
        if organization_id != str(plan["books_organization_id"]):
            raise J26RevisionToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        # The price artifact is re-hashed here, so a source that changed or
        # vanished after review stops this for free.
        require_live_lug_artifact(intent)
        sources = fixed_source_evidence()
        access_token, vault = zoho_tool.refresh_access_token(vault)
        current, live_taxes = collect_live_evidence(access_token, vault, action)
        staged_before = evidence["record"]["before_state"]
        current_prewrite = prewrite_state(current)
        staged_prewrite = prewrite_state(staged_before)
        if current_prewrite != staged_prewrite or not secrets.compare_digest(
            digest_for(current_prewrite), digest_for(staged_prewrite)
        ):
            moved = sorted(
                key for key in set(current_prewrite) | set(staged_prewrite)
                if current_prewrite.get(key) != staged_prewrite.get(key)
            )
            raise J26RevisionToolError(
                f"{label} changed after review ({', '.join(moved) or 'unknown'}). No PUT was "
                "issued and this plan is not locked; stage a new plan."
            )
        if live_taxes != evidence["tax_rows_used"]:
            raise J26RevisionToolError(
                "The active Zoho tax rows this plan priced have changed since review. No PUT was "
                "issued and this plan is not locked; stage a new plan."
            )
        fresh_evidence = build_revision(action, current, intent, live_taxes, sources)
        if stable_projection(fresh_evidence) != stable_projection(evidence):
            raise J26RevisionToolError(
                f"The reviewed projection no longer matches what the live {label} would produce. "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        fresh_payload = fresh_evidence["put_payload"]
        # The write allowlist runs here too, so a payload it would reject is a
        # free refusal rather than a permanently burned plan.
        require_put_allowed(
            action, "PUT", record_path(action, target["record_id"]), organization_id,
            evidence["put_payload"], fresh_payload, evidence,
        )
    except Exception as exc:
        zoho_tool.append_receipt(
            f"zoho_j26_403_{action}_refused_before_lock",
            f"record={label}; plan={plan_path}; sha256={plan['sha256']}; "
            "write_attempted=false; locked=false; email_sent=false",
        )
        raise J26RevisionToolError(
            f"The {action} was refused BEFORE any write and BEFORE the replay lock. "
            f"Record: {label}. No PUT was issued and no email was sent. Reason: {exc}"
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action=action, go=go,
        kind=PLAN_KINDS[action], record_id=target["record_id"], started_utc=utc_now().isoformat(),
    ), exclusive=True)
    write_attempted = False
    try:
        write_attempted = True
        result = send_put(
            access_token,
            str(vault["api_domain"]),
            "PUT",
            record_path(action, target["record_id"]),
            organization_id,
            evidence["put_payload"],
            fresh_payload,
            evidence,
            action,
        )
        verify_result(
            action, result.get(record_envelope_key(action)), evidence, "PUT response", full=False
        )
        verified = get_record(access_token, vault, action)
        verify_result(action, verified, evidence, "Fresh read-back", full=True)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        write_lock(lock, owner_authority.attempt_record(
            owner_authority.STATUS_INDETERMINATE, plan_sha256=plan["sha256"], action=action, go=go,
            reason=str(exc), kind=PLAN_KINDS[action], record_id=target["record_id"],
            write_attempted=write_attempted,
        ))
        zoho_tool.append_receipt(
            f"zoho_j26_403_{action}_indeterminate_needs_restage",
            f"record={label}; write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise J26RevisionToolError(
            owner_authority.explain_outcome(
                f"The {action}", owner_authority.STATUS_INDETERMINATE,
                f"Record: {label}. A PUT was ISSUED -- the live record state is unconfirmed. No email "
                "was sent; this tool has no mail transport. Nothing was deleted, voided, restatused, "
                f"rolled back, cleaned up or attempted a second time. Reason: {exc}",
                money=True,
            )
            + " The re-stage reads the live record first and shows what landed."
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action=action, go=go,
        kind=PLAN_KINDS[action], record_id=target["record_id"],
    ))
    zoho_tool.append_receipt(
        f"zoho_j26_403_{action}_committed_verified",
        f"record={label}; status={target['status']}; "
        f"lines={evidence['record']['final_line_count']}; "
        f"total={evidence['expected_totals']['total']} {target['currency_code']}; "
        f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": action,
        "kind": PLAN_KINDS[action],
        "record_id": target["record_id"],
        "record_number": target["record_number"],
        "record_status": target["status"],
        "currency_code": target["currency_code"],
        "original_lines_preserved": evidence["record"]["original_line_count"],
        "appended_lines": [row["name"] for row in evidence["appended_lines"]],
        "final_line_count": evidence["record"]["final_line_count"],
        "sub_total": evidence["expected_totals"]["sub_total"],
        "tax_total": evidence["expected_totals"]["tax_total"],
        "tax_certainty": evidence["expected_totals"]["tax_certainty"],
        "total": evidence["expected_totals"]["total"],
        "other_record_untouched": evidence["other_record_untouched"],
        "creates_zoho_item": False,
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
        "plan_spent": True,
        "approval_message_utc": approval_message_utc or go.sent_utc or "not stated",
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
    }, ensure_ascii=False, indent=2))


def command_commit_purchase_order_revision(args: argparse.Namespace) -> None:
    command_commit(args, ACTION_PURCHASE_ORDER)


def command_commit_estimate_revision(args: argparse.Namespace) -> None:
    command_commit(args, ACTION_ESTIMATE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_po = commands.add_parser("stage-purchase-order-revision")
    stage_po.add_argument("--input", required=True)
    stage_po.set_defaults(func=command_stage_purchase_order_revision)
    commit_po = commands.add_parser("commit-purchase-order-revision")
    commit_po.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit_po, money=True)
    commit_po.set_defaults(func=command_commit_purchase_order_revision)
    stage_estimate = commands.add_parser("stage-estimate-revision")
    stage_estimate.add_argument("--input", required=True)
    stage_estimate.set_defaults(func=command_stage_estimate_revision)
    commit_estimate = commands.add_parser("commit-estimate-revision")
    commit_estimate.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit_estimate, money=True)
    commit_estimate.set_defaults(func=command_commit_estimate_revision)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (J26RevisionToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
