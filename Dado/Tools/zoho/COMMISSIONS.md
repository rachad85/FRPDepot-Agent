# Zoho commission records — FRP Depot (Dado)

Split out of `CLAUDE.md` on 2026-08-11. **Nothing here was edited** — these are the
commission records verbatim as they stood in CLAUDE.md, de-indented only.

WHY THEY MOVED: this material was ONE bullet of 36,170 chars — 44% of CLAUDE.md —
and had pushed that file to 81,944 chars. hermes truncates a context file at
~65,280 for Dado's model (272K window x 4 chars/token x 6%; see
`agent/prompt_builder.py::_dynamic_context_file_max_chars`) and it drops the
MIDDLE, keeping head + tail. The omitted middle was landing inside these very
records, so the detail was already invisible to her while still costing the
budget that pushed other sections out.

READ THIS BEFORE TOUCHING ANY ZOHO WRITE TOOL. The constraints that govern them
are Golden Rule 3 in CLAUDE.md and rule 3 in SOUL; the per-tool contract facts,
refusal conditions, approval words and measured Zoho behaviours are here.

## Index

- **2026-08-07** — `zoho_inventory_classification_tool.py` commissioned for the one fixed `Catalog…
- **2026-08-08** — `zoho_inventory_item_tool.py` received one fixed additional operation for FRP FW PIPE…
- **2026-08-10** — `zoho_inventory_price_tool.py` commissioned for EXISTING-item sales rates on the FNPT…
- **2026-08-10** — `zoho_invoice_revision_tool.py` commissioned to revise ONE EXISTING Books invoice with…
- **2026-08-10** — (follow-on; Rachad answered "Yes" to adding creation): the SAME named tool gained a…
- **2026-08-10** — `zoho_customer_quote_tool.py` gained the ONE narrow ESTIMATE UPDATE Rachad commissioned…
- **2026-08-10** — `zoho_email_template_tool.py` commissioned after live testing proved Zoho Books ANDROID…
- **2026-08-11** — Rachad chose option (1), and after the blocked `Clone` capture proved Zoho only…
- **2026-08-11** — two defects fixed in `zoho_banking_reconciliation_tool.py`, both found while checking…
- **2026-08-11** — `zoho_sales_order_tool.py` commissioned — Rachad instructed Dado to proceed client…
- **2026-08-11** — (same day, later): *** RACHAD CORRECTED THE TAX — ONTARIO HST 13%, NOT GST 5%. *** His…


---

2026-08-07: `zoho_inventory_classification_tool.py` commissioned for the
one fixed `Catalog Classification` item dropdown and assignment of only
its three fixed values. Field creation uses the exact live UI request shape;
assignments use the verified item custom-field serializer. The independent
safety suite passes 25/25, including refusal before network without Rachad's
one-word `APPROVED`. The first creation plan was staged locally; no Zoho
write occurred.
2026-08-08: `zoho_inventory_item_tool.py` received one fixed additional
operation for FRP FW PIPE group `96274000000034779`: rename SIZE option
`96274000000034781` from `30` to `30\"`, preserving every ID and every
other group/item field. The implementation is hard-coded to the two linked
30-inch items, full-state fingerprinted, replay-locked before its one PUT,
and independently tested with all 120 Zoho tests passing. Rachad approved
plan `20260808T164957Z_group_option_rename_9d6ba1d7.json` on 2026-08-08.
Zoho rejected the single PUT with HTTP 400, code 15: `Please ensure that
the "attributes" has less than 100 characters.` A separate live GET then
proved the complete group state unchanged and the option still `30`. The
plan remains permanently replay-locked; no retry and no dependent
WooCommerce Pipe plan were made.
2026-08-10: `zoho_inventory_price_tool.py` commissioned for EXISTING-item
sales rates on the FNPT D411/D470 couplings only. It writes exactly one
field, `rate`, on items whose live SKU begins with
`FNPTCOUPLING-DERAKANE411-` or `FNPTCOUPLING-DERAKANE470-`, at exactly
supplier USD cost x 3.6 (Decimal ROUND_HALF_UP, two decimals, CAD). Cost/
purchase rate, stock, name, SKU, status, creation, deletion and every batch
route are unreachable. Its approval word is byte-exact `APPROVED` — no
`.strip()`, no case fold, deliberately stricter than its siblings. The PUT
payload is exactly `{name (preserved unchanged), rate}`; `name` is the one
preserved identifying field Zoho requires on an item PUT, proven by the
classification tool's 206 verified live item PUTs, and the read-back proves
it did not move. Protected fingerprint = every returned item field except
the rate family (rate/sales_rate/pricebook_rate/default_price_brackets/
sales_margin, which Zoho recomputes from rate and which are each verified by
explicit rule) and `last_modified_time`. BATCHES ARE NOT ATOMIC: one PUT per
line, no retry, and any failure or indeterminate result locks the whole plan.
Tests: 55 new + 175 across the whole Zoho suite, all passing; WooCommerce
suite 561 passed / 1 skipped. A 26-item plan
(`20260810T051851Z_fnpt_sales_rate_update_2fe8ea90ff05f294.json`, 20 increases,
6 decreases) was STAGED ONLY on 2026-08-10 — zero Zoho writes, zero Woo
writes, no commit lock — and awaits Rachad's own `APPROVED`. Six existing
8-inch D411/D470 variations are excluded because their supplier cells are
blank; no cost was inferred.
2026-08-10: `zoho_invoice_revision_tool.py` commissioned to revise ONE
EXISTING Books invoice with ONE atomic `PUT /books/v3/invoices/{id}`. Only
`customer_id` (to an ALREADY EXISTING customer — creation stays solely in
`zoho_customer_quote_tool.py`), `reference_number`, `date`, `due_date`,
`billing_address_id`/`shipping_address_id` owned by that live customer,
`notes`, `terms`, and per EXISTING line `quantity`, `rate`, `discount`,
`description`, `tax_id` may change. NO OMISSION-BASED DELETION: every live
line is always resent once, in order, with its own line_item_id and item_id;
adding, removing or substituting a line is refused by the schema, the plan
validator, the write allowlist AND the read-back. Invoice number, status,
currency, exchange rate, balance/payments/write-offs, adjustments, shipping
charges and custom fields are unreachable, as are create, delete, void,
mark-draft, mark-sent, submit, approve, reject, mail, reminder, payment,
credit-note, attachment, template and every bulk route. IT HAS NO MAIL
TRANSPORT AT ALL — a test asserts the source contains no mail/status/
lifecycle route and exactly one `urlopen` call site. It refuses any invoice
that is not exactly `draft` or `sent`, or that carries a payment, credit,
write-off, package, shipment or recurring profile, and it refuses line-value
changes on a sales-order-linked invoice (they would desync fulfilment).
Totals are predicted only where Zoho's result is deterministic and the plan
says so; the byte-exact protected fingerprint exempts a key ONLY when the
plan genuinely moves it, so a PO-only revision keeps every line, total and
the customer inside the fingerprint. Approval word is byte-exact `APPROVED`.
ONE attempt: any failure, timeout or indeterminate result permanently locks
the plan. Scope `ZohoBooks.invoices.UPDATE` was added to the PREPARED list
only — NOT YET LIVE: Rachad must run PREPARE_DADO_ZOHO_ACCESS.bat, create
the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. There is
deliberately no invoice CREATE/DELETE/ALL/fullaccess scope. Tests: 76 new,
251 across the whole Zoho suite, all passing; WooCommerce 561 passed /
1 skipped. Six deliberate mutations of the tool (weakened approval, dropped
line, unpreserved invoice number, non-exclusive lock, skipped fingerprint
check, unrestricted status) were each caught by the suite. BUILD AND TESTS
ONLY — the live vault was not touched, no plan was staged, and no invoice
was changed. Motivating case, NOT approval: INV-000051 (96274000001559012,
sent) where Ralmax asked for SHM PO 0000031 and billing to SHM Marine
Constructors JV — SHM does not exist as a contact yet, and the tax treatment
is unresolved, so nothing was staged.
2026-08-10 (follow-on; Rachad answered "Yes" to adding creation): the SAME
named tool gained a SECOND and final plan action, `create_draft_invoice` —
one new invoice per plan, one `POST /books/v3/invoices`, then a fresh live
read that must show status exactly `draft`. Stage with `stage-create`;
`commit` dispatches on the plan's action. ZOHO'S OWN AUTO-NUMBERING assigns
the number: `invoice_number` is absent from the POST allowlist and the
string `ignore_auto_number_generation` does not appear anywhere in the
module (a test asserts it). It requires an EXISTING ACTIVE customer whose
live name matches the stated one, addresses owned by that customer, and
EXISTING ACTIVE Zoho items on every line — no free-text or unlinked lines,
and no customer/item/tax/settings write of any kind. Every stated value
(quantity, rate, discount, description, tax ID) needs its own `source`
string; both dates must be stated so nothing is inferred. The customer's
currency is preserved and neither currency nor exchange rate is in the
payload allowlist. A repeated item line is refused unless each such line
carries its own distinct description. An independent Decimal half-up
calculation of line totals, discount, tax and grand total is shown before
approval and asserted on read-back WHERE DETERMINISTIC — a tax group or
compound tax is labelled ESTIMATE and deliberately not asserted, because
Zoho rounds each component separately (this matters: Troy Dualam Services
bills on the GST+QST group). Price precision comes from the live
organization record; when absent it defaults to 2 and totals are claimed
exact only if no line needed rounding at all. Read-back also verifies
`is_emailed` is false and that no shipping charge or adjustment appeared.
If the POST succeeds but the read-back is missing or not draft, the tool
reports indeterminate WITH the invoice ID when known and NEVER cleans up,
deletes, voids, changes status or retries. Both transports now funnel
through ONE `urlopen` call site (`_perform`), so the source still holds
exactly one network call, one `method="PUT"` and one `method="POST"`.
Scope `ZohoBooks.invoices.CREATE` was added to the PREPARED list beside
`.UPDATE` — NOT YET LIVE, same reauthorization steps. There is still no
invoice DELETE/ALL/fullaccess scope. Tests: 67 new
(`test_zoho_invoice_draft_creation.py`), 318 across the whole Zoho suite,
all passing; WooCommerce 561 passed / 1 skipped. Two pre-existing tests were
EXTENDED, not weakened, because this commission superseded them: the
"no invoices.CREATE scope" assertions (that scope is now commissioned), and
"exactly one write verb" (now one PUT plus one POST, still one urlopen).
BUILD AND TESTS ONLY — the live vault was not touched, no plan was staged,
no invoice was created and no email was sent.
2026-08-10: `zoho_customer_quote_tool.py` gained the ONE narrow ESTIMATE
UPDATE Rachad commissioned after two approved create-only draft estimates
landed high. THE DEFECT, and it is a Zoho contract fact worth keeping: Zoho
Books reads a NUMERIC line discount as a FLAT CAD AMOUNT and only a STRING
containing `%` as a percentage. Staging `10` for "10%" therefore took CAD
10.00 off each line: QT-000029 (96274000001559037) landed at CAD 15,073.96
instead of 13,680.38 and QT-000030 (96274000001558043) at 6,507.31 instead
of 5,929.02. Two fixes, one commission.
(a) CREATE PATH: percentages are now the exact string (`TDS_LINE_DISCOUNT`
= "10%"). A nonzero NUMERIC line or entity discount is refused at staging,
and `refuse_numeric_percentage_discounts` refuses a legacy plan at commit
BEFORE the vault, the token refresh and the network — so neither consumed
create plan can be replayed. Flat-amount line discounts are consequently
unreachable through this tool; write the percentage instead.
(b) CORRECTION PATH: `stage-tds-discount-correction --estimate-id` and
`commit-tds-discount-correction --plan --approval`. Exactly two estimate IDs
are reachable (`CORRECTION_TARGETS`); every other ID is refused before any
network call. Stage is GET-only: it re-reads the live estimate, requires the
exact number, reference, customer, `draft` status, exact line count/IDs/
order, `discount == 10` and `discount_amount == 10` on every line and the
exact diagnosed wrong totals, then cross-checks each line against the
IMMUTABLE original create plan (hash-verified against a constant) and the
live diagnosis artifact, and RECOMPUTES every corrected figure from the live
quantities and rates before requiring it to equal the approved totals
artifact AND the fixed approved totals. Nothing is copied on trust.
TAX BASIS, measured not assumed: Zoho computes the Quebec GST+QST on the NET
SUBTOTAL, not per line. On the live records 13,110.64 -> 1,963.32 and
5,659.76 -> 847.55 reproduce exactly on the subtotal basis (both as the
combined 14.975% and as 5% + 9.975%), while the per-line sum gives 1,963.33
— one cent out. That is why the corrected tax can be predicted at all, and
the tool refuses to stage if that reproduction ever fails.
The PUT resends the COMPLETE live line list with every `line_item_id` and
`item_id` (Zoho deletes lines a PUT omits) plus the preserved customer,
number, reference, date, notes and terms; only the discount changes. The
payload allowlist has no status, currency, tax, adjustment, shipping,
template, custom-field or mail key, and `oauth_estimate_discount_write_allowed`
accepts only PUT to the two fixed IDs — the module holds exactly one
`method="PUT"`, one `method="POST"` (the existing create) and two `urlopen`
call sites, with no DELETE/PATCH/send/status/approve route anywhere.
Commit: byte-exact approval `APPROVED`, plan hash + 24h expiry + full
re-derivation of the plan from its own staged live state, `estimates.UPDATE`
required in the saved connection, fresh GET fingerprint comparison, durable
single-use lock BEFORE the PUT, one attempt, then verification of BOTH the
PUT response and a fresh GET (identity, draft status, every line ID/order,
each line's expected discount_amount and item_total, the exact totals, and a
byte-exact protected fingerprint of every non-derived field). Any mismatch,
failure, timeout or indeterminate result leaves the plan permanently locked;
there is no retry and no rollback.
Scope `ZohoBooks.estimates.UPDATE` was added to the PREPARED list ONLY —
NOT YET LIVE: Rachad must run PREPARE_DADO_ZOHO_ACCESS.bat, create the
grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No estimate
DELETE/ALL/fullaccess scope exists.
Tests: `test_zoho_quote_discount_correction.py` (new) plus the updated
`test_zoho_tool.py`. THEY WERE NOT EXECUTED IN THE BUILD SESSION — that
session's permission layer refused every `python` invocation, so the counts
are unverified; run
`python -m unittest discover -s C:\FRPDepot\Dado\Tools\zoho -p "test_zoho*.py"`
before trusting them. BUILD ONLY — no plan was staged live, ZERO Zoho
writes, ZERO estimates changed, ZERO emails.
2026-08-10: `zoho_email_template_tool.py` commissioned after live testing
proved Zoho Books ANDROID exposes neither Android contacts nor SwiftKey
clipboard clips in its CC picker. The ONLY thing it may ever create is one
of exactly FOUR fixed organization-wide INVOICE email templates, each a
clone of the single live `Default` invoice template (ID
`96274000000000014`) with exactly one changed name and one fixed CC list:
`CC - Accounting`/`CC - Logistics`/`CC - Operations` (one address each) and
`CC - All` (logistics@, accounting@, operations@ IN THAT ORDER). Creation
is TWO-PHASE — the first plan may create `CC - Accounting` ALONE so Rachad
can prove on his phone that a non-default template is selectable and its CC
populates; the other three need his own recorded Android-test confirmation,
and the tool refuses a confirmation whose statement/source reads as
commissioning language. No other module, name, address, subset or source is
reachable; there is no update/delete/rename/set-default/associate/
attachment/PDF/sender/DKIM/relay/workflow route, no mail transport, and no
write verb at all — a test asserts the source contains no `"POST"`/`"PUT"`/
`"DELETE"`, no `urlopen`, no cookie/storage access and exactly one
`method: "GET"` call site.
Read-only discovery established the real surface: the settings route is
`#/settings/emails/templates?email_type=invoice_notification` and the two
readable endpoints are `GET /api/v3/settings/emailtemplates` and
`GET /api/v3/settings/emailtemplates/{id}` (both verified live). Zoho
publishes NO documented Books API for CREATING an email template, so the
only safe mechanism is Zoho's own native Save path, exactly as
`zoho_inventory_classification_tool.py` creates the item custom field.
*** THAT CONTRACT IS NOW CAPTURED (2026-08-10, Rachad authorized it). ***
The fixed `New` form was opened under an abort-everything interceptor,
only the fixed name and the fixed Cc dropdown option were filled, and Save
emitted exactly ONE request — `POST` to
`https://books.zohocloud.ca/api/v3/settings/emailtemplates`, empty query,
form body `JSONString=` + `organization_id`, body SHA-256
`850b177880f00f693bca3ee367a8f548b06bf252e9db873c32a591233c29b7ad` —
aborted before the network. Artifact:
`Dado\20_Working\zoho_email_template_capture\native_save_request.json`.
The payload schema is closed and now enforced in code: 7 top-level keys,
exactly one `language_content` block (`subject`/`body`/`language_code=en`/
`is_default=true`). NO header, cookie, storage value, CSRF token or
password was read. Cc entry works ONLY by opening that row's own
`.zf-ac-toggler` and clicking an exact `role=option` value; typing an
address, Enter and comma all failed live and are NOT fallbacks to retry.
*** COMMIT IS STILL NOT EXECUTABLE — THE BLOCKER MOVED FROM CONTRACT TO
CONTENT. *** Decoding that captured body proved Zoho's `New` form does NOT
clone this organization's `Default`: it loads Zoho's stock factory invoice
body. Stock reads BALANCE DUE / %Balance% / MAKE PAYMENT / Regards
%UserName% %CompanyName%; the live `Default` reads INVOICE AMOUNT /
%Total% / PAY NOW / Regards Accounting Departement. Subject and From DO
match; the bodies differ (2118 vs 2131 chars). Since the commission
requires every target to preserve `Default`'s body, creating through `New`
would either break that guarantee or succeed at the POST and then fail this
tool's own clone-fidelity read-back — leaving an ORPHAN template behind a
permanently locked plan. `require_native_form_clones_source` therefore
refuses BEFORE the replay lock, comparing the live Default's body against
what the form actually emitted. Nothing was invented and nothing created.
NEXT STEP IS RACHAD'S CALL, two options: (1) a native `Clone` control DOES
exist on the `Default` row — its menu holds exactly `Edit` and `Clone`,
verified read-only with one click on the disclosure toggle and zero write
requests — so authorize a blocked capture of the Clone form's single Save
request the way the New form's was captured; or (2) he says plainly that
the stock body is acceptable, which changes what customers receive and is
his decision, not Dado's.
Tool is now v1.1.0 / schema 2, so plans staged under the old blocker fail
closed. Tests: 103 in the email-template suite, 421 across the whole Zoho
suite, all passing. NO NEW SCOPE was needed — the tool reads through
the existing authenticated UI session and existing read-only OAuth GETs.
2026-08-11: Rachad chose option (1), and after the blocked `Clone` capture
proved Zoho only REORDERS HTML ATTRIBUTES around the PAY NOW button while
preserving the whole tree, he answered `YES`. The Clone create path is now
implemented and tested; tool is v2.0.0 / schema 3, so plans staged under
the old New-form blocker fail closed. The contract: the `Default` row's
exact `Show dropdown menu` -> exact `Clone` -> fixed name + fixed Cc option
-> Save emits ONE `POST /api/v3/settings/emailtemplates`, body SHA-256
`f6e9d14c...`, whose payload is a FLAT EIGHT-KEY object — a DIFFERENT schema
from the `New` form's nested `language_content`, so the two are decoded
separately and neither may stand in for the other. `New` is never clicked;
it stays pinned negative evidence recorded in every plan. The accepted
equivalence is ATTRIBUTE ORDER AND NOTHING ELSE: both bodies are 2,131
chars, parse to exactly 106 canonical events, every event equal;
`same_canonical_html` preserves tags, nesting, attribute names/values,
quoting, data/whitespace nodes, entities, comments and declarations exactly,
REFUSES duplicate attributes and REFUSES malformed markup. The plan's source
fingerprint still protects the live `Default` body byte-for-byte. Commit
takes the shared Zoho browser mutex BEFORE the plan replay lock (busy lane =
free refusal), arms an interceptor that aborts every non-read request, and
releases exactly ONE fully validated POST, once, no retry.
*** AN UNAPPROVED LIVE WRITE HAPPENED DURING THAT BUILD. *** Mutation-testing
deleted `@holds_zoho_browser` to prove it was load-bearing. It was — but with
it gone, `test_busy_browser_refuses_for_free_and_leaves_the_plan_reusable`
(which calls `command_commit` directly and deliberately does NOT patch
`create_template_via_ui`) had nothing left between it and the live session,
and the suite's fake vault carries the REAL org id and real `Default` id. It
drove the real browser and created live invoice template `CC - Accounting`,
ID `96274000001558092`, with no approval from Rachad. It is a faithful fixed
clone (CC exactly accounting@, BCC empty, non-default, body canonically
identical) and NO email was sent. It has NOT been deleted — there is no
delete route and that is Rachad's call. LESSON: patching the read transport
was never enough, because the create path opens its OWN playwright session;
the test module now patches `sync_playwright` itself at module scope.
*** THAT ACCIDENT ALSO EXPOSED A REAL DEFECT. *** `placeholder` was in
`SOURCE_CLONE_FIELDS`, i.e. required byte-equal to the source — but Zoho
DERIVES it from the template's own name (`Default` -> `mt_default`,
`CC - Accounting` -> `mt_cc_accounting`), so a faithful clone can never carry
the source's. Every successful create would have failed its own read-back and
orphaned a template behind a permanently locked plan. Now checked by
`derived_placeholder`. No test written against the old assumption could have
caught it.
Tests: 147 email-template, 643 whole Zoho suite, 19 `test_ui_lane_lock`, 618
WooCommerce (1 PHP-only skip), all passing; eight mutations each caught.
NO PLAN IS STAGED: `stage --action create_accounting_test` now correctly
refuses because the target already exists. The encrypted vault and profile
.env were not touched and ZERO emails were sent.
2026-08-11: two defects fixed in `zoho_banking_reconciliation_tool.py`, both
found while checking an unrelated tripwire alert. NOT a new commission — no
capability added, no guard relaxed, transport surface unchanged (still exactly
2 `urlopen` call sites, one POST, one PUT, no DELETE/PATCH; the new read goes
through the existing `zoho_tool.api_get`).
*** THE ZOHO CONTRACT FACT, measured live and worth keeping: A CATEGORIZE
CONSUMES THE IMPORTED FEED LINE AND CREATES A DIFFERENT RECORD. *** On
2026-08-11 line `96274000001423074` became transfer `96274000001558075` and
the old ID stopped resolving entirely.
(a) VERIFICATION COULD NEVER CONFIRM SUCCESS. `AFTER_SOURCE_MODES["categorize"]`
read back the PRE-WRITE source ID; `get_bank_transaction` 404s on it, falls back
to the uncategorized feed, finds zero rows, throws — and since `write_attempted`
is already true the commit recorded `indeterminate` and permanently locked a
write that HAD LANDED. A successful categorize could not reach
`committed_verified`. Now `verify_categorize_result` takes the resulting ID from
the POST response (`categorized_result_id`, which returns "" rather than guess
when the response carries no single ID) and verifies THAT record via
`get_categorized_result` — a direct GET, deliberately NOT `get_bank_transaction`,
whose feed fallback would both re-read the state a success removes and drag in
the authenticated UI session. Checked: type equals the approved
`transaction_type`; amount/date/currency equal the source before-state; both
approved account IDs present; `reference_number`/`description` preserved where
staged. The lock now carries `resulting_transaction_id`, and a FAILED commit
that Zoho nonetheless accepted records `zoho_accepted_the_write` plus that ID —
an indeterminate Zoho ACCEPTED needs a different reconcile from one it rejected.
(b) STAGING NEVER CHECKED THE ACCOUNTS WERE ACTIVE, though the account GET
already answers it: the 09:50 plan stored `is_active: False` IN ITS OWN SNAPSHOT,
staged anyway, and Zoho rejected it 400/11015 "Inactive or deleted accounts".
Nothing reached the books, but it burned a single-use plan and lock.
`require_categorize_accounts_active` now refuses both accounts at staging, and
the Airwallex recovery fixed-fact list gained `new source account status` — it
had always asserted the DESTINATION was active and never the source.
WHY IT SHIPPED: `recovery_accounts()` pinned the source account `"status":
"inactive"` and every recovery test passed with it — the live broken state
captured as a fixture — and there was no categorize commit-success test at all.
Both corrected; the fixture is `active`, matching live after Rachad reactivated
the account between the two attempts.
Tests: 43 in the banking suite (3 new), 587 across the whole Zoho suite, all
passing; WooCommerce 618 passed / 1 skipped. Both fixes were mutation-checked —
removing either makes the suite fail (3 and 8 failures respectively).
NO live Zoho call was made from this session: BUILD AND TESTS ONLY, zero writes,
zero plans staged, zero emails. The two locked plans from 2026-08-11 stay locked
and MUST NOT be retried — the transfer already exists.
2026-08-11: `zoho_sales_order_tool.py` commissioned — Rachad instructed Dado to
proceed client PO26330 from Structural Composites Technologies Ltd, create the
Sales Order and attach the original PO. ONE fixed transaction, nothing
parameterised: commands are `stage-sct-po26330` (no arguments at all) and
`commit-sct-po26330 --plan --approval`, action
`create_sct_po26330_draft_sales_order_with_attachment`. Customer
`96274000000186533`, addresses `96274000000186536`/`96274000000186538`, contact
person Bon Bacani `96274000000509126`, item `96274000000523055` (SKU
`FNPTCOUPLING-DERAKANE470-3/4"6"`) qty 2 at CAD 50.20, GST
`96274000000035512` 5%, reference `PO26330`, date 2026-08-11, required
2026-08-12, payment terms 30 / Net 30, fixed notes, CAD 100.40 / 5.02 / 105.42.
*** THE RATE IS DELIBERATELY NOT THE LIVE ITEM RATE. *** The item sells at CAD
45.72; this order is CAD 50.20 because the client PO and Rachad's own emailed
offer of 2026-08-07 both say so. The tool reads and displays the live rate but
never substitutes it and never alters the item.
*** TWO TRAPS IN THE ITEM LIST, both named and refused in code. *** The 3/4" x 8"
variation `96274000000523057` happens to sell at exactly CAD 50.20 — the PO price
— and has ZERO physical stock; and `96274000000508303` is literally named
`LEGACY — DO NOT USE` (also CAD 50.20, zero stock) and is the item the historical
SO-00020 used. Only `96274000000523055` has stock (exactly 2), and staging refuses
unless `actual_available_stock` >= 2 — the PHYSICAL figure, not the committed
projection.
*** SO-00020 IS NOT A DUPLICATE. *** It carries the identical CAD 105.42 total but
has no PO reference and used the legacy item. The duplicate sweep therefore matches
ONLY on a normalized reference of `26330`/`po26330` (any customer) or a still-OPEN
same-customer order naming the PO in its text — never on an amount. A closed
historical order is never even read in detail, and SO-00020 is not touched.
*** THE OPTIONAL-FIELD CONTRACT IS PROVED LIVE, NOT ASSUMED. *** The supplied
`zoho_readback.json` is a KEY PROJECTION written by `zoho_crosscheck.py`, not a
full Zoho response — its header keeps 19 hand-picked keys — so the absence of
`shipment_date`, `contact_persons` or `documents` in that artifact proves NOTHING.
Do not read a projection as a contract. Instead stage GET-reads one fixed existing
order (SO-00041 `96274000001071007`, read-only, never written) and asks which keys
it actually exposes. Presence is positive proof and enables the field; ABSENCE IS
"NOT PROVEN", so the field is omitted rather than guessed — and because Zoho omits
keys it has no value for, that asymmetry is deliberate. `payment_terms` is the one
key that is not optional: the PO says Net 30 explicitly, so if the live contract
does not expose it, nothing is staged. When `shipment_date` is unproven the required
date still lives in the fixed notes (`Required: 2026-08-12`), which are byte-exact
in both directions, and the plan says which placement was used.
*** CREATION AND ATTACHMENT ARE DELIBERATELY NOT ATOMIC. *** One JSON POST
`/books/v3/salesorders`, then one multipart POST
`/books/v3/salesorders/{new_id}/attachment` where `{new_id}` comes ONLY from the
create response — a pre-existing order ID is refused by the route guard. So the
order CAN exist without its attachment; the plan and the stage summary say so
before Rachad approves. The attachment is proven by GETting it back and hashing the
returned bytes against the fixed SHA-256; a JSON message instead of the file, or an
empty body, is not a verification. Attachment metadata on the order GET is checked
too where exposed. Zoho's own numbering assigns the order number
(`salesorder_number` is absent from the allowlist and read-back refuses a number
that IS the PO number); `ignore_auto_number_generation` appears once in the whole
module, inside the refused-key list.
Transport: exactly ONE `urlopen` call site behind a method allowlist of
`("GET", "POST")`, so no update or delete verb exists even to be misused. Exactly
five route families are constructible, asserted as a set by a test that parses the
module's AST. No mail/SMTP/Graph/Outlook/recipient path, no browser or CDP path.
Fingerprint drift, a late duplicate, a changed contract or a missing scope all
refuse BEFORE the exclusive lock, so they are FREE refusals that leave the approved
plan still committable — the lock is taken only immediately before the first POST.
Scope `ZohoBooks.salesorders.CREATE` was added to the PREPARED list ONLY — NOT YET
LIVE: Rachad must run PREPARE_DADO_ZOHO_ACCESS.bat, create the grant, then
REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No sales-order UPDATE/DELETE/
ALL/fullaccess scope and no Inventory sales-order write scope exists.
Tests: 95 new (`test_zoho_sct_po26330_sales_order.py`, 1 skipped where Windows
refuses to create a symlink), 806 across the whole Zoho suite, all passing;
WooCommerce 618 passed / 1 skipped. Containment tests scan the module with
comments and docstrings STRIPPED — a raw-text scan only ever proves the prose
mentions what it refuses. Eight deliberate mutations (tolerant approval, dropped
CREATE-scope check, attachment to any ID, unverified attachment hash, disabled
duplicate sweep, unrequired Draft status, unrequired physical stock, non-exclusive
lock) were each caught by the suite. Two pre-existing tests were EXTENDED, not
weakened, because this commission superseded them: both asserted
`ZohoBooks.salesorders.CREATE` was uncommissioned, and each now pins the broader
sales-order scopes as still refused.
A live-read defect was also fixed in the sweep guard: an empty `page_context`
dict was treated as "last page", which would report a PARTIAL order-book read as a
complete one. An absent `has_more_page` now fails closed.
BUILD AND TESTS ONLY — the encrypted vault, .env and live profile were not
touched, NO plan was staged, and there were ZERO Zoho writes, ZERO sales orders
created, ZERO attachments uploaded and ZERO emails.
2026-08-11 (same day, later): *** RACHAD CORRECTED THE TAX — ONTARIO HST 13%,
NOT GST 5%. *** His words: "we want to charge sale of Ontario". The tool now
charges live tax `96274000000035516` `ON HST` at 13%: sub-total CAD 100.40
(still the client PO's own), tax CAD 13.05, total CAD 113.45, Decimal
ROUND_HALF_UP. Every non-tax fact above is unchanged — customer, PO reference,
item, quantity 2, rate CAD 50.20, dates, addresses, Bon, Net 30, notes, stock
rule, PDF path/hash/size, attachment behaviour and every guard.
*** THE PO'S OWN TAX FIGURE IS NOW A SECOND DELIBERATE DEPARTURE. *** The rate
already differed from the live item rate; now the tax differs from the client
PO, which prints `GST (ITC)@5.0% CAD 5.02`. Both departures are stated with
their source in the staged plan, and `build_totals` no longer checks the tax
against the PO at all — only the sub-total is the PO's figure. Keeping the PO's
printed GST as a named constant (`CLIENT_PO_TAX_LABEL` / `CLIENT_PO_TAX_TOTAL`)
is deliberate: the difference is disclosed, not silently applied.
MANITOBA, which he asked about in the same breath: read-only, the 12 months to
2026-08-11 hold 4 Manitoba-destined Books invoices — CAD 12,100.20 net, CAD
605.01 GST, CAD 12,705.21 total, zero credit notes. With this PO's CAD 100.40
that is CAD 12,200.60, CAD 17,799.40 below Manitoba's CAD 30,000 threshold.
Manitoba Finance Bulletin RST 004 (rev. June 2024) states that threshold but
also caveats out-of-province sellers who have not paid Manitoba RST on goods
bought for resale; and CRA GST/HST Memorandum 3-3-3 (April 2026) paras 13-16
say that where the PURCHASER set the freight terms and account and the supplier
merely calls that carrier for pickup, the supplier does not retain the carrier
and delivery stays at the supplier's premises — PO26330 says Puro Collect on
SCT account 3763800. This tool neither decides nor alters any registration.
TOOL IS v2.0.0 / SCHEMA 2, so the one plan staged under the GST-5% build fails
closed. It is additionally named by SHA-256 (`SUPERSEDED_PLAN_SHA256`) so a
retry is told WHY rather than only that the plan is invalid. That plan,
`20260811T175734Z_create_sct_po26330_draft_sales_order_with_attachment_2950664b01e2366a.json`,
WAS NEVER APPROVED AND NEVER COMMITTED (no lock file was ever written); its
file is deliberately left on disk, byte-unmodified, as the record.
Tests: 100 in the SCT file (5 new, 1 pre-existing Windows symlink skip), 812
across the whole Zoho suite, all passing; WooCommerce 618 passed / 1 PHP-only
skip; `test_ui_lane_lock` 19. Three mutations were each caught: reverting the
version/schema bump (5 failures), deleting the superseded-SHA guard (1), and
putting GST 5% back (59). The strongest regression test copies the REAL
superseded plan file into a patched plan folder and drives a full
`command_commit` with the real approval word — it refuses, writes nothing,
locks nothing, and the original file is proven unchanged afterwards.
ALSO FIXED, and it was stale rather than wrong-by-design: `zoho_tool.py`
connect/reauthorize/check each printed "... and order write scopes: ABSENT"
while `ZohoBooks.salesorders.CREATE` sat in SCOPES — and `command_check`
only reaches that line after proving the saved connection holds every scope
in SCOPES, so the claim was false exactly when it printed. Those three lines
now disclose the one commissioned sales-order write and keep ABSENT for what
genuinely is absent (sales-order UPDATE/DELETE, estimate/invoice DELETE,
status/send/void/approval/payment/convert, stock adjustment). NO scope list
and NO guard changed — only the narration, pinned by a new test.
BUILD AND TESTS ONLY, again: no live stage, ZERO Zoho writes, ZERO sales
orders created, ZERO attachments uploaded, ZERO emails, vault and .env
untouched, live profile files untouched.

## 2026-08-11 — fixed 4-inch/10-inch backing-ring stock and rate merge

Rachad commissioned `zoho_backing_ring_stock_tool.py` after confirming that the
existing generic 4-inch and 10-inch Zoho items are the same products as the
photographed incoming stock and that the current order must consume those
existing item IDs. It exposes one action only. Inventory Adjustment
`96274000001556196`, dated 2026-08-11 with reference
`BACKING-RINGS-2026-08-11`, added +12 pcs to item `96274000001518002`
(`BRDN100150PSI411`) and +101 pcs to item `96274000001518014`
(`BRDN250150PSI411`). The adjustment is `adjusted` and totals CAD 18,421.50:
CAD 696.00 and CAD 17,725.50, derived from the preserved live purchase rates
CAD 58.00 and CAD 175.50. The tool then changed only those two future sales
rates to CAD 108.00 and CAD 468.00.

The three writes are deliberately non-atomic: the two-line adjustment POST
lands first, followed by one name-preserving item-rate PUT per item. The plan
locks before the first write, has one attempt, and has no retry, rollback,
delete, adjustment update, item creation/deactivation, invoice/order write,
status/approval, email, browser, WooCommerce or generic route. The only new
prepared scope is `ZohoInventory.inventoryadjustments.CREATE`; UPDATE, DELETE,
ALL and fullaccess remain absent.

Rachad approved plan SHA-256
`81d35927cbbb88318c9575bad8caa19ce495ad6a9c638e10d72a142f5275bfee`.
All three writes landed and were independently re-read live. The 4-inch item is
12 physical / -12 available for sale at CAD 108.00. The 10-inch item is 101
physical / 65 available for sale at CAD 468.00. INV-000051 / SO-00050 retained
the exact two line IDs and item IDs, quantities 24/36 and historical rates CAD
97.00/CAD 297.00. The lock state is `verified`; replay is permanently refused.
Zero duplicate items, zero order/invoice changes and zero emails.

## 2026-08-12 — temporary backing-ring 1-1/2-inch correction, superseded before approval

Rachad first corrected the photographed 1-1/2-inch black entries to ONE product,
85 pcs total, and instructed that the outside diameter not be shown. That
briefly staged `FRP BACKING RING-1-1/2\"/150PSI/D411/BLACK`, SKU
`BRDN40150PSI411-BLK`, at CAD 52.20, plan SHA-256
`30291ce09dbd505cc64f40417fed77a02ba23cc087a63b6e1342d74d6be7b884`.
It was never approved and never committed, and the later all-size colour merge
below permanently withdrew it too.

The two earlier OD-specific plans are permanently withdrawn and now refused by
full digest inside `zoho_inventory_item_tool.py`, together with the three
previously withdrawn duplicate 4-inch/10-inch plans. This temporary correction
performed ZERO Zoho writes and ZERO website writes.

## 2026-08-12 — all backing rings merged by nominal size before approval

Rachad then ruled that black and white do not matter for ANY backing-ring size.
The catalog now has exactly one colour-neutral item per nominal size, with no
colour or OD in the name, SKU or description. The corrected stock totals are:
1-inch 218, 1-1/2-inch 85, 2-inch 32, 3-inch 39, 4-inch 12, 6-inch 22,
8-inch 238, 10-inch 101, 12-inch 47 and 14-inch 32 — 826 units total.

The 4-inch and 10-inch quantities remain on their already-existing generic item
IDs. Eight fresh colour-neutral item-create plans are staged for the other
sizes; they contain 713 units and preserve the already sourced Fei prices. All
16 superseded duplicate/OD/colour plans are permanently refused by full digest
inside `zoho_inventory_item_tool.py`. The 19-test item-tool safety suite passes.
The correction itself made ZERO Zoho writes and ZERO website writes.

Rachad then answered the complete eight-plan review with his own exact one-word
`APPROVED`. The named tool created all eight independent items, and fresh live
GETs verified every item ID, name, colour-neutral SKU, active inventory/goods
status, unit, taxable/sell/purchase flags, accounts, CAD rate, unique live SKU,
and zero starting stock. IDs:
`BRDN25150PSI411` 96274000001556231;
`BRDN40150PSI411` 96274000001556243;
`BRDN50150PSI411` 96274000001556255;
`BRDN80150PSI411` 96274000001556267;
`BRDN150150PSI411` 96274000001556279;
`BRDN200150PSI411` 96274000001556291;
`BRDN300150PSI411` 96274000001555023; and
`BRDN350150PSI411` 96274000001555035.
No colour or OD appears on the live records. The 713 units of physical stock
remain pending and require a separately commissioned, staged and approved fixed
adjustment; this item-create approval did not authorize that stock write. ZERO
website writes and ZERO emails.

## 2026-08-11 — eight new backing-ring items, tentative landed valuation stock plan

Rachad commissioned a second fixed stock tool for ONLY the eight newly created
colour-neutral generic items. Their exact photographed quantities are 218, 85,
32, 39, 22, 238, 47 and 32 pcs for 1, 1-1/2, 2, 3, 6, 8, 12 and 14-inch —
713 pcs total. `zoho_backing_ring_eight_stock_tool.py` can create ONE eight-line
positive Inventory Adjustment and has no item PUT/PATCH, price/rate write,
order/invoice write, website route or mail transport.

Valuation follows Rachad's direct instruction: preserve Fei's quoted USD unit
cost, add a separate tentative 20% landing allowance, then convert using the
Bank of Canada 2026-08-11 daily average, 1 USD = CAD 1.3927. Exact converted
unit values are carried through quantity multiplication and each CAD line total
is rounded once, half-up. Total tentative valuation: CAD 78,816.51. This does
not alter the separate supplier USD cost x 3.6 CAD selling rates and does not
write purchase rates.

The first staged plan, SHA-256
`fa5d1ab504f45993ea5d595f13575938ec1194a608b0ce61bcdd0171fbeb099b`,
was withdrawn before approval because its descriptive text said "Fei Fei". It
made zero writes and is permanently refused by full hash. Tool v1.0.1 passes its
58-test targeted/regression set and the full 1,032-test Zoho suite (4 expected
skips). The corrected current read-only plan is
`20260812T013942Z_eight_backing_ring_tentative_landed_stock_fd77238cca9e.json`,
SHA-256 `fd77238cca9e0552c216e9b79cac8569354cea1dfb310e5b53ff906aa01b696b`,
expiring `2026-08-13T01:39:42.022760+00:00`. Live staging proved all eight items
still had zero stock and zero purchase rate, the fixed reference was absent,
and the saved CREATE scope was ready.

Rachad then replied with his own exact `APPROVED`. The one POST created
Inventory Adjustment `96274000001555048` in Adjusted status and added all 713
units in the correct eight-item identity/order/quantities, but Zoho ignored each
posted `item_total`: every live line and the adjustment total are CAD 0.00. The
verifier therefore locked plan
`fd77238cca9e0552c216e9b79cac8569354cea1dfb310e5b53ff906aa01b696b`
`indeterminate`; no retry is allowed. Three fresh read-only adjustment/item
rounds proved the result exact and stable: 713 physical units loaded, purchase
rates still CAD 0.00, selling rates still CAD 50.40 / 52.20 / 57.60 / 72.00 /
216.00 / 342.00 / 727.20 / 918.00, every protected item field unchanged, and
zero inventory value instead of the planned tentative CAD 78,816.51. Root
cause: the fixed generic merge succeeded because those pre-existing items
already carried nonzero purchase rates (CAD 58.00 and 175.50); these new items
carried purchase rate zero, and Zoho derived quantity-adjustment valuation from
that zero rate rather than honoring the submitted `item_total`. Any accounting
valuation correction requires a separate commission and approval. ZERO website
writes and ZERO emails.

## 2026-08-12 — fixed eight-item value-only correction commissioned and staged

Rachad answered `Proceed` to the recommended separate valuation correction after
the zero-valued quantity load. `zoho_backing_ring_eight_valuation_correction_tool.py`
is fixed to ONE action: create ONE eight-line Inventory Adjustment with
`adjustment_type: value` for the same eight item IDs. Its only line write field is
`value_adjusted`, at CAD 5,100.62 / 2,059.80 / 855.67 / 1,303.57 / 2,206.04 /
37,786.74 / 15,866.75 / 13,637.32, totaling exactly CAD 78,816.51. The values
retain Fei's original USD costs, the separate 20% tentative landing allowance,
and Bank of Canada FXUSDCAD 1.3927 for 2026-08-11. Full-precision CAD unit bases
are multiplied by quantity and each line is rounded once, half-up. The independent
Fei USD x 3.6 CAD selling-rate rule is preserved and is not used for valuation.

The payload has no `quantity_adjusted` or `item_total`. It cannot update or retry
source adjustment `96274000001555048`, change any quantity, item, purchase/sales
rate, stock field, invoice, order or website record, or send email. Staging and
commit both require the source adjustment to remain exactly Adjusted with the
fixed eight line identities/order, 713 total pieces and CAD 0.00 value; every
fixed item must retain its exact stock fields, zero purchase/valuation fields and
approved selling rate; and the correction reference must remain absent. Staging
performs three bounded read-only rehearsals and refuses moving state.

Commit requires Rachad's own later exact unpadded uppercase `APPROVED`, then
repeats the three-read rehearsal before its lock. The ONE POST is atomic at the
request level and the plan is locked before it. Any failure, timeout or
indeterminate result permanently locks the plan with no retry, rollback, delete
or cleanup. There is one `urlopen` call site and no PUT/PATCH/DELETE, browser,
mail, status or lifecycle route.

Tests: 19 fixed-tool tests passed; 77 focused/regression tests passed; the complete
Zoho discovery suite passed 1,051 tests with 4 expected skips. Read-only staging
produced plan
`20260812T030535Z_eight_backing_ring_zero_value_correction_2fa9a355a426.json`,
logical SHA-256
`2fa9a355a426540aaf72078c4002467a386ebf907c26b40d421a20c8dc04c594`,
expiring `2026-08-13T03:05:35.453677+00:00`. Three identical live rounds proved
the source remains 713 pieces / CAD 0.00, all eight item stock/rate protections
remain exact, the correction reference is absent, and the CREATE scope is ready.
Rachad then answered this plan with his own exact `APPROVED`. The one POST created
VALUE Inventory Adjustment `96274000001555109`. The immediate verifier saw
`is_inventory_valuation_pending: true`, permanently locked the plan
`indeterminate`, and correctly made no retry. A later fresh read showed processing
complete: status `adjusted`, `adjustment_type: value`, pending false, and all eight
fixed lines at CAD 5,100.62 / 2,059.80 / 855.67 / 1,303.57 / 2,206.04 /
37,786.74 / 15,866.75 / 13,637.32, totaling exactly CAD 78,816.51. Every
`quantity_adjusted` is absent. Three saved fresh reads are business-identical and
the protected source adjustment plus all eight item stock, purchase rate and
selling rate projections match the staged plan exactly. The only two reconciliation
false alarms were local verifier defects: Zoho serialized CAD 2,059.80 as `2059.8`,
and a later fingerprint accidentally included the observation labels 1/2/3. Local
Decimal normalization and exclusion of those labels proved all three saved reads
identical; no further Zoho call was made. The commit lock deliberately remains
`indeterminate` / no-retry as the permanent attempt record. STATUS: LIVE RESULT
EXACT AND STABLE; CAD 78,816.51 value added, ZERO quantity changes, ZERO item/rate,
order/invoice/website writes and ZERO emails.

---

## 2026-08-12 — SHM customer prerequisite + INV-000051 fixed correction (PLAN A LANDED; PLAN B REJECTED / NO BUSINESS CHANGE)

Rachad chose **"Yes — build/test the fixed customer + invoice correction flow"** on
2026-08-12 after being shown all three live blockers. **BUILT AND TESTED ONLY: no
plan staged, ZERO Zoho writes, ZERO emails, ZERO Outlook drafts, ZERO website
writes, browser never contacted.** Every future write is a separate immutable plan
needing Rachad's own later message containing exactly unpadded uppercase `APPROVED`.
Dado never supplies that word and never infers it.

WHY IT EXISTS: Elaine Iverson asked for INV-000051 to be billed to **SHM Marine
Constructors JV** against client PO **0000031**, and Rachad ruled the sale is a
customer collection from Brockville, so it carries **Ontario HST 13%** — not the
GST 5% on both lines, and not the inconsistent tax printed on the PO. The general
`invoice_revision` action refuses that record on two counts and **both refusals
stay exactly as they are**: `ALLOWED_STATUSES` is still exactly `(draft, sent)`
(this invoice is `overdue`), and it still refuses line changes on a sales-order-linked
invoice (this one is linked to SO-00050). Tests assert both.

**PLAN A — `zoho_customer_quote_tool.py`, `stage-shm-inv000051-customer` /
`commit-shm-inv000051-customer`.** ONE `POST /books/v3/contacts`. No business
parameter is accepted anywhere: the contact name, company, type, sub-type, the
343A Bay St / Victoria / BC / V8T1P5 / Canada / 250-590-7072 billing address and
the single primary Elaine Iverson contact are fixed in `SHM_CUSTOMER_PAYLOAD`, and
the payload is compared byte-for-byte against that constant at the transport.
No shipping address, website, tax registration, payment term or second contact is
invented. Duplicate detection walks **every** contact unfiltered and proves
completeness from Zoho's own `page_context.has_more_page`; a missing or non-boolean
page context, a mismatched page number, or either ceiling is a REFUSAL, never a
partial scan reported clean. The walk runs AGAIN fresh at commit, so a customer
created between staging and approval still stops it. Fresh GET readback proves ID,
name, company, customer/active state, CAD, every supplied billing field, the
billing `address_id` and the exact primary contact and email.

**PLAN B — `zoho_invoice_revision_tool.py`, `stage-inv000051-shm-correction` /
`commit-inv000051-shm-correction`.** ONE `PUT /books/v3/invoices/96274000001559012`.
Reachable changes are exactly five: `customer_id` → the live exact SHM record,
`reference_number` `SO-00050` → `0000031`, `billing_address_id` → the address that
SHM itself owns, and each line's `tax_id` GST `96274000000035512` → ON HST
`96274000000035516`. Both lines are resent once, in order, carrying their own
`line_item_id`, `item_id` and `salesorder_item_id`. Independent Decimal half-up
target, computed per-line AND whole-subtotal and required to agree:
**13,020.00 + 1,692.60 = 14,712.60**. Staging takes three bounded read-only rounds
whose canonical fingerprints must agree across invoice, customer, addresses, ON HST
and the linked order; the rehearsal digest is re-bound to a FRESH read at commit.

*** THE SALES ORDER IS NEVER WRITTEN, AND THE ONE THING THAT DOES MOVE IS
DISCLOSED. *** There is no sales-order write route, method or scope anywhere, and
the saved connection holds no sales-order UPDATE scope at all. SO-00050's own
business fields — customer, lines, quantities, rates, taxes, totals, status,
invoiced_status, addresses, terms, custom fields — are proven byte-for-byte
unchanged. BUT the live probe found the order carries a **read-only mirror of this
invoice** (`salesorder.invoices`) holding the invoice's `reference_number`, `total`
and `balance`. It necessarily reflects the approved change, so it is excluded from
the fingerprint and verified by explicit rule instead: identity and status must not
move, `reference_number` may become `0000031` and `total`/`balance` may become
`14712.60`, and nothing else may move at all. Every plan carries that disclosure
verbatim, and validation refuses a plan that drops it.

*** FOUR LIVE FACTS THE READ-ONLY PROBE CHANGED, EACH ONE A BURNED PLAN AVOIDED. ***
1. The invoice carries a **header-level mirror of the line tax** (`tax_id` /
   `tax_name` / `tax_percentage`, live: GST / 5.0) that Zoho recomputes the moment
   a line tax changes. Left inside the byte-exact fingerprint, a CORRECT write
   would have read as "changed outside the approved fields" and locked the plan
   `indeterminate` — the exact defect class that locked the 2026-08-11 backing-ring
   valuation plan. It is exempt and asserted against ON HST 13% instead.
2. **`is_emailed` is already TRUE** — the invoice was sent. "No email sent" is
   therefore verified as `is_emailed` UNCHANGED plus `reminders_sent` UNCHANGED,
   never as `is_emailed == false`, which would have failed on a correct write.
   `reminders_sent` is deliberately kept INSIDE the fingerprint so a reminder
   leaving Zoho is caught.
3. **`billing_address_id` and `shipping_address_id` both read as null on a GET**;
   the invoice exposes embedded address objects instead. The result is verified
   against the fixed PO values AND against the SHM customer's own live billing
   address — not by address ID.
4. Invoice lines carry **live item stock mirrors** (`available_stock`,
   `available_for_sale_stock`, `committed_stock`, `stock_on_hand`) on the two
   backing-ring items Rachad has been adjusting. They move with unrelated inventory
   activity, so they are excluded from the pre-write drift projection only; a test
   pins that moving stock does not block staging.

BLANK SHIPPING ADDRESS: staging refuses unless the invoice's shipping address is
blank AND the SHM customer has none, so Zoho has nothing to copy. It is verified
blank again after the write. Zoho's exact behaviour on a customer change cannot be
proven without performing the write; the design refuses fail-closed beforehand and
locks `indeterminate` / no-retry afterwards rather than papering over a surprise.

*** PLAN A AND PLAN B ARE DELIBERATELY NOT ATOMIC. *** Plan A creates a real
customer. If Plan B is then never staged, never approved, or fails, that customer
REMAINS. Neither tool has any delete, deactivate, rename, merge, rollback, cleanup
or retry route by design — a delete capability is far more dangerous than a spare
customer record. Both plans, both staged summaries and both risk notes say so, and
validation refuses a plan whose `risk.atomic_with_invoice_correction` /
`risk.atomic_with_customer_plan` is not `false`.

TWO DEFECTS FOUND AND FIXED DURING THE BUILD: (a) offline plan validation could not
detect a hand-edited `put_payload`, since the payload is derived from live state —
`validate_shm_plan` now re-derives the PUT body from the plan's OWN staged
before-state plus the recorded customer and address IDs, so a tampered payload is
refused when the plan is READ, not only later at the fresh preflight (five
mutations pinned by tests); (b) the stable-state rehearsal was not re-bound at
commit, so an approved plan could have carried a rehearsal of some other state past
the write.

FOUR PRE-EXISTING TESTS WERE UPDATED, NONE LOOSENED: they assert the modules' TOTAL
write surface by exact count or exact set (parser choices, `method=` literals,
`urlopen` sites), which a new commissioned action necessarily moves. Each was
retightened to the new exact inventory — no inequality, no subset check, no skip —
and every forbidden route, verb and mail marker they assert stays asserted. The
invoice module still holds exactly ONE `urlopen` site: the new transport reuses
`_perform`.

Tests: the new `test_zoho_inv000051_shm_correction.py` runs 142 / 142 passed;
existing invoice modules 156 / 156; existing customer-tool dependents 332 run,
331 passed, 1 expected skip; the complete `test_zoho*.py` discovery suite
**1,193 run, 1,189 passed, 4 expected skips, 0 failures, 0 errors**; `py_compile`
clean on all 7 touched modules. A GET-only rehearsal against the live records
accepted the real INV-000051 unchanged, confirmed ON HST `96274000000035516` Active
at exactly 13%, confirmed SO-00050 and its 2 lines, derived 14,712.60, and
confirmed the SHM customer was absent before Plan A. Machine-readable build result:
`Dado\20_Working\inv000051_shm_correction_build_result.json`.

**LIVE OUTCOME 2026-08-12.** Rachad separately approved Plan A, SHA-256
`f72d47637f22e751c311d776f21e65b1451a5159e12d21c0e82643b7d2277d0d`.
Its one POST created and fresh-read verified active customer **SHM Marine
Constructors JV**, contact ID `96274000001569002`, owned billing address ID
`96274000001569004`, and primary Elaine Iverson contact-person ID
`96274000001569003`. The customer plan is verified and permanently replay-locked;
zero email was sent. The customer remains regardless of the invoice outcome.

Rachad then separately approved Plan B, SHA-256
`c6b09bab21f52d911e2bf8301e79eb8d6a089fc7610a594ea7974d1884815ca4`.
The lock was written before its one PUT. Zoho rejected that PUT with HTTP 400,
code 4116: **"You cannot change the customer name when converting an Quote to a
recurring invoice. Kindly create a new invoice instead."** The plan is permanently
locked `indeterminate` / no-retry; no rollback or second attempt was made. Three
fresh read-only reconciliation rounds then proved stable and byte-exact against
the staged pre-write projections: **no invoice or sales-order business change
landed**. INV-000051 remains Overdue under Ralmax, reference SO-00050, GST 5%,
subtotal CAD 13,020.00, tax CAD 651.00 and total/balance CAD 13,671.00; both lines,
quantities, rates, descriptions and links remain unchanged. SO-00050 and its invoice
mirror also remain unchanged. Zero email was sent. Reconciliation artifact:
`Dado\20_Working\inv000051_failed_put_reconciliation_20260812.json`, SHA-256
`22f7301475f47c90bf93dade1733d8ab1a7fb94f13259c991546a75c5e5111da`.

Zoho's stated route is a **new invoice**, not another customer-change retry. Any
replacement Draft invoice is a separate action and needs its own newly staged plan
and Rachad's own new exact `APPROVED`. The existing Overdue invoice is not voided,
deleted, credited or restatused by any commissioned tool, and no such action may be
inferred from either approval above.

**REPLACEMENT DRAFT LIVE OUTCOME 2026-08-12.** After Zoho's code-4116 refusal,
Rachad separately authorized preparation and then approved Draft-creation plan
SHA-256 `4d4cbff46c882f8bcf5dede8fd3d0601bd2b5a9169a3431a9fe780f8e9144b43`.
Its one POST created **INV-000053** (`96274000001569012`). The immediate verifier
permanently locked the plan `indeterminate` / no-retry because Zoho's invoice GET
returned no `billing_address_id`, even though its embedded billing-address object
carried the exact approved address values. No retry, cleanup, delete, void, status
change or email occurred. Three fresh read-only rounds then proved every approved
business value exact: status Draft; customer SHM Marine Constructors JV; PO 0000031;
date/due date 2026-08-10; billing 343A Bay St, Victoria BC V8T1P5, Canada,
250-590-7072; two fixed item lines at 24 x CAD 97.00 and 36 x CAD 297.00, zero
discount, preserved descriptions, ON HST 13%; subtotal CAD 13,020.00, tax
CAD 1,692.60, total/balance CAD 14,712.60; zero shipping/adjustment/payment; and
`is_emailed` false. INV-000051 and SO-00050 remained unchanged in all three reads.
The only field varying across three additional full GETs was Zoho's regenerated
`invoice_url`; after excluding only that non-business secure-payment URL, the full
business projection hash was stable at
`5fd318ee708e2a3eaccae525a66a708435e0c03808075e217c22e76e6acf6fdb`.
The permanent plan lock remains `indeterminate` / no-retry as the attempt record.
Final reconciliation artifact:
`Dado\20_Working\shm_replacement_draft_final_reconciliation_20260812.json`,
SHA-256 `6381e57b76d2ed0cf91b74a5b63110051c9004a69fc53d8c65aed67fadc2e4b6`.
2026-08-12: `zoho_historical_client_po_reference_tool.py` commissioned after the
read-only client-PO audit. Rachad selected "Yes - commission and build the
reference-only repair tool". STATUS 2026-08-12: the six INVOICE plans were later
staged, approved together by Rachad's own exact `APPROVED`, committed independently,
and all six verified. Exactly six invoice PUTs landed; six permanent locks are
`verified`; zero Sales Orders and zero emails were touched. Sales Orders remain
deferred because their UPDATE scope conflicts with the SCT PO26330 creation tool as
described below. The live vault was READ but never rewritten with a new grant.
WHAT IT REPAIRS: six historical Sales Orders and their linked invoices display an
INTERNAL quote or order number where the customer's own PO belongs. That field is
NOT internal — Zoho prints it to the customer, as `Ref# :` on a Sales Order PDF
and `P.O.# :` on an invoice PDF. Proven live read-only on 2026-08-12: SO-00013
shows `Ref# : QT-000012` and INV-000014 shows `P.O.# : SO-00013`.
Exactly TWELVE records are writable, one field (`reference_number`), one record
per immutable 24-hour plan, each needing Rachad's own later byte-exact `APPROVED`:
SO-00013 `96274000000317001` QT-000012 -> `104662`; INV-000014 `96274000000312107`
SO-00013 -> `104662`; SO-00016 `96274000000409073` QT-000015 -> `PO5072`;
INV-000018 `96274000000411047` SO-00016 -> `PO5072`; SO-00019 `96274000000466136`
QT-000016 -> `PO5079`; SO-00021 `96274000000575001` QT-000017 -> `PO26078`;
INV-000023 `96274000000579007` SO-00021 -> `PO26078`; SO-00040 `96274000001030001`
QT-000022 -> `2127`; INV-000039 `96274000001052009` SO-00040 -> `2127`; SO-00044
`96274000001140080` blank -> `4500021643`; INV-000043 `96274000001140095` and
INV-000045 `96274000001212003` SO-00044 -> `4500021643`.
*** THE PREFIXES ARE LOAD-BEARING. *** The recovery tool normalized two of these
to bare digits (`5079`, `26078`). The externally evidenced spelling wins: the
customer's own message or attached PO, and for PO5079 the already-correct linked
invoice. A test asserts `5079` and `26078` are not among the targets.
INV-000020 `96274000000552009` is a fixed VERIFICATION-ONLY dependency that must
keep reading `PO5079`; `select_record` refuses it by name, `perform_put` refuses
it by ID, and it is the evidence for SO-00019's spelling. The 15 ambiguous
recoveries, 5 no-evidence cases and 18 additional invoice links are unreachable —
a test walks the real audit and recovery artifacts and refuses every one of the
40+ non-fixed IDs they contain, as well as INV-000051, INV-000053 and SO-00050.
*** THE WRITE CONTRACT IS PROVEN FROM ZOHO'S OWN OPENAPI, NOT GUESSED. *** The
published bundle (`openapi-all.zip`, SHA-256
`109a2ee32299d8fbc3a65b52475eb1fc9875961f087d115ff7cfcbb7d7039a45`) is pinned file
by file in `Dado\20_Working\historical_client_po_reference_contract\`
(`sales-order.yml` `7202417f…e294ea4`, `invoices.yml` `83a757a8…da1828ed`) and
re-hashed at every stage and commit. It states `PUT /salesorders/{id}` requires
only `customer_id`, while `PUT /invoices/{id}` requires `customer_id` AND
`line_items`. So an ORDER payload is exactly `{customer_id, reference_number}` and
carries NO line at all — omission cannot reach a line because no line is sent —
while an INVOICE payload resends every live line once, in original order, with its
own `line_item_id` and `item_id`. Nothing is ever omitted in the hope that Zoho
preserves it. That directory sits under the gitignored `20_Working`, so integrity
does not depend on git: a mismatch fails closed at runtime.
PROTECTED FINGERPRINT: every returned business field byte-for-byte. Four categories
leave it, each handled by explicit rule — `reference_number` (the one changed field,
checked in both directions), `last_modified_time` / `last_modified_by_id` (Zoho
stamps these on any update and they are reported in the receipt), the regenerated
secure `invoice_url` (three consecutive live GETs proved it changes while all
business fields remain identical; it is recorded but excluded from equality), and
the read-only MIRROR of a linked record's reference
(`salesorder.invoices[].reference_number` and
`invoice.salesorders[].reference_number`). The mirror is replaced by a sentinel and
then checked against a CLOSED set — that linked record's own fixed before or
target, and for INV-000020 exactly `PO5079`. It is never simply excused: without
this, committing an order plan would make an already-staged invoice plan of the
same case read as drift.
STAGING IS GET-ONLY: four artifact hashes, the Canadian API domain, the FRP Depot
Books organization, the record-type update scope (refused BEFORE any read if it is
not live, with the exact reauthorization steps — readiness is never faked), a
THREE-READ stable rehearsal of the record plus every fixed dependency, the exact
before reference (an already-correct record is reported and NOT staged; a third
value is drift), and a bounded read-only fetch of the rendered customer document.
If the caption cannot be read the plan is labelled NOT PROVEN and is never
committable.
COMMIT: byte-exact `APPROVED` checked before the vault is even opened; full plan
re-validation including a re-signed-plan semantic check; a fresh full re-read that
must match the complete protected fingerprint and the before reference, refusing
FREE and BEFORE the lock; an exclusive `O_EXCL` lock taken immediately before the
ONE `PUT`; then a fresh GET proving identity, linkage, the exact target, the
unchanged fingerprint, unchanged line identity/order, unchanged totals and balance
and every dependency; then a FRESH rendered PDF that must show the client PO under
this document type's own caption. THE CAPTION ANCHOR MATTERS: a bare substring
search would pass if the PO merely appeared in a note, and the old value often
legitimately appears elsewhere (INV-000014's old reference IS its order number).
Any failure at any point locks the plan `indeterminate` / `no_retry`; there is no
retry, rollback or cleanup route. PLANS ARE INDEPENDENT AND NOT ATOMIC AS A BATCH
— an earlier approved plan that succeeded stays applied if a later one fails, and
every plan says so.
CONTAINMENT, proven by AST tests: exactly ONE `urlopen` call site; the only
constructible verbs are `GET` and `PUT`; exactly 12 writable routes; no
POST/PATCH/DELETE; no mail/SMTP/Graph/Outlook transport; no browser/CDP/playwright
path; no attachment, status, lifecycle, payment, credit or conversion route; no
`subprocess`/`shutil`/`ctypes`/`socket` import; no generic ID, value, endpoint,
payload, method or module argument; and no batch command. The only selector is a
fixed `--record-key`. PDF text is read with PyMuPDF (`fitz`); Poppler is never
invoked.
SCOPES: `ZohoBooks.invoices.UPDATE` already existed. `ZohoBooks.salesorders.UPDATE`
was added to the PREPARED list only — NOT YET LIVE (verified live 2026-08-12:
invoices.UPDATE present, salesorders.UPDATE absent). Rachad must run
PREPARE_DADO_ZOHO_ACCESS.bat, create the grant, then REAUTHORIZE_DADO_ZOHO.bat and
CHECK_DADO_ZOHO.bat. No DELETE/ALL/fullaccess, status, send, void, approval,
payment, package, shipment, conversion, attachment-write, customer-write,
item-write or Inventory sales-order write scope was added. The three connector
status lines that claimed sales-order UPDATE was ABSENT were corrected — a stale
status line is the same false comfort this tree has been bitten by before.
*** OPEN BLOCKER, RACHAD'S CALL, NOT SILENTLY RESOLVED. ***
`zoho_sales_order_tool.py` lists `ZohoBooks.salesorders.UPDATE` in its own
`FORBIDDEN_SALESORDER_SCOPES` and REFUSES TO RUN while the saved connection holds
it. Once the new scope goes live, that SCT PO26330 tool refuses at staging. The
brief forbids modifying that module, so its guard was left byte-identical rather
than quietly relaxed, and
`test_zoho_sct_po26330_sales_order.py::test_only_the_one_create_scope_is_used` now
asserts the conflict so it is executable rather than hidden. NOTHING IS BLOCKED
TODAY: that tool is itself BUILD AND TESTS ONLY with no plan staged, and its own
CREATE scope is also prepared but not live. Three options: reauthorize and accept
that it refuses until separately amended; amend its forbidden list under its own
commission first; or repair the six INVOICES now, since invoices.UPDATE is already
live, and defer the six orders.
FIVE PRE-EXISTING TESTS WERE EXTENDED, NOT WEAKENED, because this commission
superseded them — the same pattern the 2026-08-11 sales-order commission used.
Each asserted salesorders.UPDATE was absent or uncommissioned; each now pins the
still-forbidden broader scopes and, where relevant, proves the new scope is
unreachable from that sibling tool. `zoho_invoice_revision_tool.py` and
`zoho_sales_order_tool.py` themselves are byte-identical, and the revision tool's
refusal of paid invoices is intact and asserted by a test here.
Tests after the live secure-URL regression repair: 107 focused
(`test_zoho_historical_client_po_reference_tool.py`, 0 skipped), 1,301 across the
whole Zoho suite, all passing with 4 pre-existing environment
skips (2 missing cached workbook, 2 symlink privilege). Coverage includes 26
protected-field mutation classes, 16 re-signed plan mutations and 7 targeted
weakening mutations (tolerant approval, widened ID set, skipped fingerprint,
skipped rendered check, unlocked PUT, second attempt, payload widening at three
independent gates), each caught.
Bounded read-only rehearsal touched 17 Zoho GET endpoints across all thirteen
records and four rendered PDFs; every ID, number, customer, status, currency and
linkage matched the scope artifact. Disclosed rather than reported as zero POSTs:
`refresh_access_token` POSTs to the Zoho accounts token endpoint, which is the
standard credential refresh for any authenticated read and changes no business
record. Findings worth keeping: SO-00044/INV-000043/INV-000045 are USD not CAD;
SO-00044's `shipped_status` is `fulfilled` where the other five are `shipped`
(both pinned per record); no record carries a system lock; and SO-00044 already
holds the client PO as an attached document named
`PurchaseOrder4500021643ESTIMATE-08577.pdf`, independently corroborating
`4500021643`. Build result:
`Dado\20_Working\historical_client_po_reference_tool_build_result.json`.

LIVE INVOICE OUTCOME 2026-08-12: Rachad approved the six displayed immutable plans
with exact `APPROVED`. The tool made one PUT per invoice and independently verified
the live API Reference# plus a fresh rendered PDF caption `P.O.#`: INV-000014 ->
`104662`; INV-000018 -> `PO5072`; INV-000023 -> `PO26078`; INV-000039 -> `2127`;
INV-000043 and INV-000045 -> `4500021643`. All six locks are `verified`; every
protected field, status, currency, total, balance, customer, tax, date, address and
line identity/order stayed unchanged. The linked Sales Orders were read and proved
unchanged but were not written. Exactly six Zoho business writes, zero Sales Order
writes and zero emails. The plans are permanently replay-locked. Commit result:
`Dado\20_Working\historical_client_po_six_invoice_commit_result_20260813.json`,
SHA-256 `210dc268e7d069e462256dd19115ed7ce7df6863571f9fda4ff288718ddf013d`.
