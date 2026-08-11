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
