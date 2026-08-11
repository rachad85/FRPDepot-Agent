# FRP Depot — company fit profile (Dado's fact sheet)

Dado: read this at the start of every session. Add facts here the
moment Rachad states them (dated). NEVER invent a fact not on this
sheet — ask instead.

## Company basics (Rachad to fill / Dado to capture as told)
- Legal name:
- What we sell: Catalog-based FRP products. Mailbox evidence includes FRP pipe,
  fittings, stub flanges, saddle tees, grating, profiles, and separately quoted
  coatings/unlisted items.
- Resin standard (Rachad, confirmed 2026-08-08): use D411 unless Rachad explicitly requests another resin. Use `D411` in customer-facing selectors, item names, quotes, and catalog labels. In technical/manufacturer documents, use `Derakane 411-350 (D411)` on first mention when the exact grade matters. D411 and Derakane 411-350 are the same catalog option and must not be listed separately. Stock availability does not authorize changing the resin.
- Location / warehouse: Business/contact address repeatedly shown as 4507
  Ferguson Dr., Brockville, Ontario, Canada K6T 1A9. Warehouse status is not
  confirmed.
- Website / email domain: www.frpdepots.com / frpdepots.com.
- Rachad's FRP Depot email address: info@frpdepots.com (verified by Microsoft Graph)
- Outlook live-sweep rule: before declaring an RFQ open, fetch the full live conversation and verify the latest non-draft message is inbound, then cross-check Zoho Books transaction/payment status and Zoho Inventory price/stock data (learned 2026-07-23 after a false open-RFQ report).
- Official Outlook signature rule: every customer-facing draft must use the verified HTML signature extracted from a real Sent Item, including the inline FRP DEPOTS logo and all contact details; the bundle is `Dado/20_Working/outlook_signature/official_signature_bundle.json` (source Sent Item dated 2026-07-21, verified 2026-07-23).
- Outlook reply-thread rule (Rachad, 2026-07-23): every email reply draft must use **Reply All** from the latest live external non-draft message in the existing Outlook conversation—never a new standalone message. Preserve the existing subject, conversation identity, quoted history, and externally appropriate To/Cc roles; add the new reply above the history and the official HTML signature once. Check Drafts first, keep only one active response draft, then reopen it and verify the thread, recipients, body, signature, attachments, and that no newer source message arrived before reporting it ready.
- Internal-copy rule (Rachad, 2026-08-10): every outbound email must copy at least one of `logistics@frpdepots.com`, `accounting@frpdepots.com`, or `operations@frpdepots.com`, or all three, as Rachad selects for that message. These addresses should be readily selectable in his Android email/Zoho workflow rather than manually copied and pasted.
- Zoho Android recurring-CC finding (live-tested 2026-08-10): Zoho Books Android did not expose Android contacts in its Cc picker even with Contacts permission, and its recipient field would not accept Microsoft SwiftKey clipboard clips. The live invoice-email API exposed only customer-specific contact persons and one organization-wide `Default` email template. Official Zoho Books Canada documentation confirms email templates can carry preset Cc/Bcc addresses. The scalable solution is four module-specific clones of the Default template (`CC - Logistics`, `CC - Accounting`, `CC - Operations`, `CC - All`), not adding FRP Depot staff to every customer. Test one clone on Android before creating the rest; do not associate these templates with individual customers or make one universal default.
- Chase-own exception (Rachad, 2026-08-02): when a thread he started has NO external message at all (nobody ever answered his email), the follow-up chase draft is a Reply All under HIS OWN latest sent message in that thread — `outlook_tool.py reply-all --chase-own` — keeping the original recipients and quoted history. The tool refuses this path the moment any live external message exists, and refuses it for anything but a chase. Ruled when the 2026-07-31 digest found 5 genuine follow-ups and could draft none.
- Rachad's standard email signature block (verified from repeated Outlook Sent Items):
  Rachad Homsi
  CEO

  www.frpdepots.com
  Direct : +1 613-704-7963
  4507 Ferguson Dr.
  Brockville, Ontario, Canada
  K6T 1A9

## Commercial rules
- Competitor (Rachad, 2026-08-10): FRP Supply / frpsupply.com is an FRP Depot competitor. Competitor prices are public retail evidence only and must be matched by product type, size, length, pressure, resin and connection before comparison.
- Currency: Verified transactions use CAD and USD. No single default currency is confirmed.
- Pricing currency (Rachad, 2026-08-07, approving the SCT coupling draft): "Prices
  are all in CAD". Stated over the stocked-coupling price comparison, so it governs
  the prices Dado presents and quotes. It does NOT restate the currency of a Zoho
  record or a supplier invoice — read those from the record itself.
- FNPT pricing rule (Rachad, 2026-08-10): for the current FNPT supplier quotation,
  calculate the target CAD selling price as `supplier USD cost × 3.6`. The multiplier
  includes currency conversion, shipping, handling, and margin. Flag every current
  Zoho/Woo selling price below that target for review; do not change a price without
  a separate approved write plan. This is scoped to FNPT until Rachad applies it
  elsewhere.
- FNPT rollout order (Rachad, 2026-08-10): hold all WooCommerce FNPT price changes
  until every supplier price is available. Set the Zoho selling rates first, then
  prepare the website push.
- Existing Zoho sales-rate writes: NO LONGER BLOCKED for FNPT (Rachad commissioned
  the named `zoho_inventory_price_tool.py` on 2026-08-10). It changes only the
  sales rate `rate` on existing items whose live SKU begins exactly with
  `FNPTCOUPLING-DERAKANE411-` or `FNPTCOUPLING-DERAKANE470-`, to exactly
  supplier USD cost x 3.6 (decimal half-up, two decimals, CAD), through the same
  stage-then-commit flow with Rachad's own exact uppercase one word `APPROVED`.
  Its approval word is compared byte-exact — no trimming, no case folding. Its
  batches are NOT atomic: one independent PUT per line, and any failed or
  indeterminate line stops the run and permanently locks the whole plan. Sales
  rates on every other item family, and every other Zoho field, remain blocked.
  The first 26-item plan was approved and committed on 2026-08-10. Live readback
  verified all 26 sales rates at supplier USD cost × 3.6; 20 increased and 6
  decreased. The plan is replay-locked. Six current 8-inch D411/D470 variations
  remain excluded because their supplier cells are blank; no cost was inferred.
- Payment terms default: No global default confirmed. Customer terms are account/order-specific; supplier terms are negotiated per PO/invoice.
- Shipping terms default: No global default confirmed. Observed orders use Ex Works, FCA, or customer-account collect arrangements.
- Quote validity default: No general default found in the mailbox; Rachad must confirm.
- Price list location / source of truth: Sales replies direct customers to the
  website catalog for listed pipe and fittings; exact source-of-truth rule still
  requires Rachad's confirmation.
- Stock availability rule (Rachad, 2026-07-30): use **Physical Available for Sale** only. Accounting stock and billed-but-unreceived purchase-order quantities must never be represented as physically in stock. Zoho's **Inventory Summary and phone quote item picker display accounting availability even when the organization mode is Physical Stock** (live-confirmed with SKU PIDN150150PSI411: 600 ft displayed versus physical 0 ft). For accurate quotes, open the item and use **Overview > Physical Stock > Available for Sale**, or use the read-only Inventory API field `actual_available_stock`.
- Sales tax follows the customer's address/jurisdiction. Rachad stated on 2026-08-10 that FRP Depot is not registered to collect BC PST. This registration fact alone does not settle the tax treatment of a BC order; confirm the current CRA place-of-supply and BC out-of-province PST rules, plus the delivery terms, before changing or presenting tax.
- Zoho invoice capability commissioned by Rachad on 2026-08-10: a named approval-gated tool may (1) revise existing invoices without sending them and without changing their status, and (2) create new invoices in **Draft** status only. Every save is staged and requires Rachad's later exact one-word `APPROVED`; sending/emailing, deleting, voiding, marking sent, payments, credits and automatic approval remain unreachable. Build and OAuth permission work are pending; commissioning itself granted no live permission and caused no Zoho write.
- Zoho invoice REVISION tool status (2026-08-10): **BUILT AND TESTED ONLY — no permission granted, no plan staged, no invoice changed.** `Dado\Tools\zoho\zoho_invoice_revision_tool.py` revises ONE existing invoice with ONE atomic PUT, changing only `customer_id` (to an ALREADY EXISTING customer), `reference_number`, `date`, `due_date`, customer-owned `billing_address_id`/`shipping_address_id`, `notes`, `terms`, and per existing line `quantity`, `rate`, `discount`, `description`, `tax_id`. Every live line is always resent once in order with its line_item_id and item_id, so nothing can be deleted by omission; adding, removing or substituting a line is refused. It has **no mail transport at all**, cannot change the invoice number, status, currency, exchange rate, balance/payments, adjustments, shipping charges or custom fields, and cannot create, delete, void, mark-draft or mark-sent. It refuses any invoice that is not exactly `draft` or `sent` or that carries a payment, credit, write-off, package, shipment or recurring profile. Approval is byte-exact `APPROVED`; one attempt only, and any failure or indeterminate result permanently locks the plan. The OAuth scope `ZohoBooks.invoices.UPDATE` is in the PREPARED list only and is **not live** until Rachad runs PREPARE_DADO_ZOHO_ACCESS.bat, creates the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat.
- Zoho DRAFT INVOICE CREATION status (2026-08-10, follow-on to the revision build): **BUILT AND TESTED ONLY — OAuth reauthorization still pending, no plan staged, zero Zoho writes, zero emails, no invoice created.** Part (2) of the commissioned capability is now implemented as the second action, `create_draft_invoice`, of the SAME named tool `Dado\Tools\zoho\zoho_invoice_revision_tool.py` (stage with `stage-create`, commit with the same `commit`). It creates ONE new invoice with ONE `POST /books/v3/invoices` and verifies live that the result is in exactly `draft` status. **Zoho's own auto-numbering assigns the number** — no caller-supplied number and no `ignore_auto_number_generation` exists anywhere in the module. It requires an EXISTING ACTIVE customer whose live name matches what was stated, addresses owned by that customer, and EXISTING ACTIVE Zoho items on every line (no free-text or unlinked lines; no item, customer or tax creation). Quantity, rate, discount, description and tax ID are accepted only with an explicit `source` string per value. Both `date` and `due_date` must be stated so nothing is inferred. The customer's own currency is preserved; currency and exchange rate are not in the payload allowlist. A duplicate item line is refused unless every line for that item carries its own distinct description. An independent Decimal (half-up) calculation of each line total, the discount, tax and grand total is shown before approval and asserted on read-back wherever Zoho's result is deterministic — a **tax group or compound tax is shown as an ESTIMATE and deliberately not asserted**, because Zoho rounds each component separately. Read-back verifies status exactly `draft`, the auto number, customer, currency, addresses, every line's item/order/quantity/rate/discount/description/tax/line-total, the dates, reference, notes, terms, the totals, that `is_emailed` is false, and that no shipping charge or adjustment appeared. If the POST succeeds but the read-back is missing or not draft, it reports an indeterminate failure **with the invoice ID when known and never attempts cleanup, deletion, voiding, a status change or a retry**. Approval is byte-exact `APPROVED`, checked before the lock, the vault, the token and the network; the plan is locked before the POST and permanently after any attempt. OAuth scope `ZohoBooks.invoices.CREATE` was added to the PREPARED list alongside `.UPDATE` and is **not live** until Rachad runs PREPARE_DADO_ZOHO_ACCESS.bat, creates the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. There is still no invoice DELETE/ALL/fullaccess scope.
- INV-000051 revision request (2026-08-10) is CONTEXT, NOT APPROVAL. Ralmax (Josh Caulfield) asked to put the SHM PO on the invoice and forward it to Elaine Iverson. Two blockers, both unresolved: **SHM Marine Constructors JV does not exist as a Zoho contact** (creating it belongs solely to `zoho_customer_quote_tool.py`, never to the revision tool), and the **tax treatment is unresolved** pending delivery/carrier facts — so no tax change may be inferred or staged. The forward itself is a DRAFT-only email action; the revision tool cannot send anything.
- Troy Dualam Services Inc. (customer ID 96274000000060019) is in Quebec and must use the combined **GST + QST** tax group (14.975%; Zoho tax ID 96274000001071139).
- Troy Dualam Services Inc. receives an automatic **10% discount** on every FRP Depot order/estimate (Rachad, 2026-07-30).
- Manufacturer-confirmed pipe construction: Fei wrote on 2025-11-26, **“all pipe sizes adopt filament winding method.”** Her 2025-11-04 attachment, `Filament Wound Pipe Lamination.pdf`, clarifies that this means the **structural roving layer** is filament-wound, while the **inner surface, chopped-strand-mat interior layer, and C-veil + UV outer surface** are hand-laid. Do not describe this as separate hand-laid axial sections. The document covers 1–36 in pipe and cites ASTM D2992 design basis / ASTM D2996 manufacture.


## People
- Rachad Homsi — owner. Telegram 891365639.
- (others as learned)

## Dated notes
- 2026-07-22: Dado commissioned. Systems: Outlook email, Zoho
  Books/Invoice, Zoho Inventory. Quotes are catalog/price-list based.
- 2026-07-23: Mailbox evidence verified WordPress/WooCommerce for website
  orders, Stripe for card payments, and Zoho Payroll/Forte for payroll/direct
  deposit. Sales inquiries are routed to sales@frpdepots.com.
- 2026-07-23: Fulfillment commonly requires a packing/shipping packet with
  origin, package count, dimensions/weight, labels, photographs, packing list,
  commercial invoice, BOL/AWB, pickup contact/hours, and carrier account.
- 2026-07-23: Rachad commissioned the named **FRP Depot Zoho Customer & Quote
  Draft Tool**. It may create customers and draft estimates in Zoho Books only
  after Rachad approves the record and every quote number. It cannot send,
  update, delete, mark sent, accept, or decline.
- 2026-07-23: Rachad commissioned the named **FRP Depot Zoho Inventory Item
  Catalog Tool**. It may create approved items and update only approved existing
  item names/SKUs. It cannot delete items, change existing prices, adjust stock,
  change quantities, or write orders, invoices, transfers, or payments.
- 2026-07-24: Rachad confirmed that Troy Dualam is a legitimate arm's-length
  customer/vendor in FRP Depot's own Zoho. A Troy Dualam name/domain on an FRP
  Depot Zoho contact or transaction is not by itself a company-wall breach and
  must not be removed or excluded automatically. Dado may audit the FRP-side
  record while continuing to avoid TDI's separate Zoho, files, mailbox, and
  internal data.
- 2026-07-24: Rachad designated Dado as his reference for his personal Google
  account. Dado's reference cache is local-only at
  `%LOCALAPPDATA%/FRPDepot-Google/reference/google_reference.sqlite`, outside
  the FRP Depot Git repository. Use it for historical reference but verify
  current-state questions against live Google. Gmail read/drafts, Drive
  read-only, Analytics read-only, Calendar read-only, Contacts read-only, and
  Search Console read-only are connected and verified. The TDI screen applies
  to GMAIL results (for Drive it is superseded by the 2026-07-25 note below).
- 2026-07-25: Rachad removed every Drive restriction — "Yes go ahead no
  restrictions and if there's anything thats restricting you let's…" (Telegram
  00:03; the logged message is truncated there). Drive is UNRESTRICTED: no
  company filtering in the Google tools, the indexer, the backfill, or the
  cache. Gmail keeps its TDI screen. Do not reintroduce a Drive wall unless
  Rachad asks for one.
- 2026-07-24: Rachad commissioned the named **FRP Depot WooCommerce Audit &
  Approved Catalog Change Tool**. It may read products, variations, safe store
  settings, system status, shipping, payment gateways, customers, and orders.
  Customer/order API reads use positive projections that exclude identifying,
  payment, note, metadata, and credential fields before they leave the store.
  Product and variation create/update writes are allowed only through an unchanged
  full-SHA-256 plan shown to Rachad, expiring after 24 hours, and committed after
  his exact approval phrase for that digest. Creations are forced to draft; plans
  are replay-locked before one write and verified by live readback. No delete,
  customer, order, payment, refund, coupon, webhook, plugin/theme, user, stock,
  publication, or arbitrary-setting writes are commissioned.
- 2026-07-24: Rachad closed FRP Depot's Airwallex accounts. The July 23, 2026
  uncategorized deposits of CAD 78,146.27 and USD 21,642.71 into FRP Depot's
  Desjardins accounts are internal closure transfers from those Airwallex
  accounts, not revenue. Preserve the source-account history; match/categorize
  them as bank transfers only after confirming the corresponding Airwallex
  outgoing entries.
- 2026-07-26: Rachad commissioned TWO named Google write tools (Telegram 19:04,
  "let's give you write access"). Drive is no longer read-only. (a) **Investments
  workbook tool** (`google_investments_tool.py`) — the ONE file he means by "the
  investments log" is `My Drive / My Files / Rachad / Bussiness Folder /
  `Investements.xlsx` (19:00, "From now on when i tell you update the investments
  log its this one"); allowed edit is a cash-profit row in the `Pistavo Labs`
  section. (b) **Loans tool** (`google_loans_tool.py`) — Google Sheet `Loans`;
  `CCIVS` fills the next blank A:B row with a dated negative repayment. On
  2026-07-31 Rachad directly commissioned and confirmed the live `Stefe` tab
  (spelled exactly that way): its fixed C6:E43 table may receive one staged row
  containing date, negative amount and plain-text description. On 2026-08-04,
  Rachad directly commissioned the existing `In-Laws` tab (spelled with the
  hyphen): its fixed A7:C61 table may receive the same staged three-cell row;
  row 62 is the pinned note and the B5 balance formula counts B7:B234. Both
  operations remain stage-then-commit under Hard Rule 3. Rachad's approval word
  is ONE PLAIN WORD — `APPROVED` / "Proceed" (20:17); never ask him to copy a
  checksum. Sheet screenshots omit "live Google Sheets" wording and crop at the
  latest payment.
- 2026-07-27: FRP Depot GA4 access is live and read-only. Google Analytics
  account `2499934` ("Northnet Media") exposes property `529941333` ("FRP
  Depots"). A live Data API audit returned historical frpdepots.com traffic,
  engagement, form, cart, and purchase events. This supersedes the 2026-07-24
  finding that no FRP Depot property was accessible.
- 2026-07-23 correction from Rachad: An RFQ sweep must cross-check the live
  Outlook thread against Zoho Books transaction/payment status and Zoho
  Inventory item, price, and stock data before calling it open. Formal RFQs are
  prepared as DRAFT estimates inside Zoho Books for Rachad to inspect and send.
  Only when Rachad asks for quick item pricing is the response prepared as an
  Outlook draft without a Zoho estimate. Dado never sends either one.
- 2026-07-27: Rachad treats Dado-prepared drafts that he deliberately leaves
  unsent or abandons as closed tasks. Do not remind him about those drafts again.
  A genuinely new inbound message or an explicit instruction from Rachad creates
  a new task; otherwise the closed item stays silent.
- 2026-07-27: Rachad commissioned a local Custom Quotes Log for quotations sent
  by Outlook outside Zoho. The system of record is
  `C:\FRPDepot\Dado\30_Memory\custom_quotes_log.csv`. Record a quote only after
  live Outlook Sent Items confirms the message and non-inline PDF attachment;
  drafts are not logged as sent. Use `Dado\Tools\quotes\custom_quote_log_tool.py`.
  The first verified entry is `CQ-2026-0001`, KENZ reference 2600AM-KE4288.
- 2026-07-29: Rachad's official HANDWRITTEN signature (for signing PDF forms —
  separate from the Outlook HTML signature bundle above) is saved canonically
  OUTSIDE the repo at `%LOCALAPPDATA%\FRPDepot-Signature\`:
  `Rachad_official_signature_source.jpg`,
  `Rachad_official_signature_transparent_CANONICAL.png`, and
  `official_signature.json`. He asked for it to be kept (Telegram 10:28, "can
  you make sure this is saved for future ?") after supplying the source image
  himself. Reuse that file; do not re-derive a signature from Drive or a Sent
  Item.
- 2026-07-29: Filled-PDF delivery rule, learned after Rachad replied "the form
  you sent me is empty not filled" (Telegram 11:52) about a form the receipts
  already called filled and visually verified. A filled AcroForm can render
  blank in the viewer he opens. Before calling a form done: flatten it (or
  render with annotations disabled), visually verify THAT file, and deliver the
  flattened copy. A structural field-value check is not evidence of what he
  will see.
- 2026-07-31: Currency wording on his personal loan/investment sheets — Rachad,
  approving the Stefe line: "Approved but don't use CAD just dollar sign"
  (Telegram 20:32). Write amounts as `$50` in these plans, screenshots and
  Telegram reports; do not label them CAD. Zoho/invoice figures are unaffected.
- 2026-08-02: Recurring supplier payments — Rachad asked "How much are we paying
  monthly to. near north company? Check quick book" (Telegram 17:30) and named
  the vendor himself: "This company **Northnet Media**" (17:39). Two things
  follow. (a) Northnet Media is a company FRP Depot pays on a recurring monthly
  basis, and he also asked for "the ones for Troy" — so the same vendor bills
  Troy Dualam. Note it is the SAME name as Google Analytics account `2499934`
  above; he has not said whether they are one entity, so do not assert it.
  (b) **QuickBooks is not connected to Dado.** Her financial system of record is
  Zoho Books/Invoice. When he says "check QuickBooks", answer from Zoho and name
  that source, or tell him QuickBooks is not connected — never present a Zoho
  figure as a QuickBooks figure, and never state a monthly amount you cannot
  point at a live record for.
- 2026-07-30: Standing duty — Rachad asked for the four-month lead-time reorder
  analysis to run "every 1st of the month automatically going forward"
  (Telegram 19:00). It runs READ-ONLY from `zoho_reorder_analysis.py` on the 1st
  at 08:00 (Hermes cron `96e29b6507b1`) and the order candidates go to Rachad.
  Confirm it actually fired on the next 1st; if it did not, tell him and fix the
  schedule rather than re-running it by hand and staying silent.
- 2026-08-06: Rachad wants Dado to serve as FRP Depot's executive operations
  manager because he runs multiple companies and needs proactive help. He intends
  to grant the required access; expansions remain implemented through named,
  approval-gated tools rather than credentials shared in chat.
- 2026-08-06: After a live Zoho/Woo comparison, Rachad decided to retain Zoho
  item groups. Catalog corrections will proceed one issue at a time: Dado shows
  the evidence and staged plan, then commits only after Rachad replies to that
  plan with the one-word approval `APPROVED`.
- 2026-08-06: Rachad authorized a paired SKU-correction workflow using the
  existing named Zoho item and Woo catalog tools. It must use one combined
  staged plan, his one-word `APPROVED`, and live read-back verification in both
  systems; authorization to build the workflow is not approval of a SKU change.
- 2026-08-07: Rachad commissioned `zoho_inventory_category_tool.py`. Its write
  scope is limited to creating item categories and assigning existing items to
  a category, with assignments staged in batches of about 20. Every category
  creation or assignment batch requires its own staged plan, Rachad's one-word
  `APPROVED`, and live read-back verification. It may not change item groups,
  names, SKUs, prices, stock, accounting, or any other item field, and it may
  not delete categories. Authorization to build the tool is not approval of a
  category creation or assignment plan.
- 2026-08-07: Category-tool discovery confirmed live read-only
  `GET /inventory/v1/categories`, but Zoho's published Inventory OpenAPI does
  not document category creation or `category_id` on item-update requests.
  Both category commit commands are therefore hard-disabled; 33 safety tests
  pass and a real commit invocation refuses before plan, token or service
  access. The safe next route is an authenticated UI-backed implementation.
  `CONNECT_DADO_ZOHO_UI.bat` opens a dedicated Edge profile stored outside the
  repo at `%LOCALAPPDATA%\FRPDepot-Zoho-UI\`; Rachad signs in directly there
  and never puts credentials in chat.
- 2026-08-07: Rachad commissioned the named **FRP Depot Zoho Banking
  Reconciliation Tool** (`zoho_banking_reconciliation_tool.py`) for staged
  matching and categorizing of imported bank lines, including verified internal
  transfers and ordinary expenses, staged unmatch/uncategorize corrections, and
  staged updates limited to correcting the source/destination account on an
  existing transfer. Every operation requires its own immutable plan and his
  one-word `APPROVED`. The only added OAuth grants are
  `ZohoBooks.banking.CREATE` and `ZohoBooks.banking.UPDATE` beside the existing
  banking read scope; banking DELETE/ALL, bank-account and bank-rule changes,
  direct standalone bank-transaction creation, and ad-hoc writes remain
  prohibited.
- 2026-08-07: A live read-only Zoho Books + Outlook audit found the two
  2026-07-23 Airwallex closure receipts are correctly typed as internal
  `transfer_fund` entries and are not revenue, but their **source accounts are
  wrong**. CAD 78,146.27 is transaction `96274000001533058` into
  `Chequing account (C)` / imported line `96274000001423076`; USD 21,642.71 is
  transaction `96274000001535012` into `USD Desjardins corporate build-up
  account` / imported line `96274000001423074`. Both currently point from the
  active CAD `FRPDepot Inc.` Digital Commerce Bank account
  `96274000000097003`. Rachad's live Sent Item of 2026-07-24 explicitly says the
  sources must remain the inactive `AWX_FRPDepot Inc._CAD`
  (`96274000000149537`) and `AWX_FRPDepot Inc._USD` (`96274000000149257`)
  accounts. The wrong source also makes the USD transfer header report CAD at
  exchange rate 1. Any correction must use a separate
  `update_transfer_accounts` plan from the named banking tool and Rachad's own
  `APPROVED`; it must preserve each destination, amount, date, transfer type,
  reference and description.
- 2026-08-07: Rachad confirmed the current active banking/payment accounts are
  **Desjardins CAD, Desjardins USD, Stripe, and PayPal**. Live Zoho names/IDs are
  `FRP Depots - Desjardins` (`96274000001411002`, CAD),
  `USD Desjardins corporate build-up account` (`96274000001409012`, USD),
  `Stripe Clearing` (`96274000000035815`, CAD), and
  `PayPal Clearing` (`96274000000035828`, CAD).
- 2026-08-07: The global Books `GET /banktransactions` ledger listing is NOT a
  complete open-bank-feed audit. It returned only matched, categorized and
  manually-added rows and missed a Desjardins CAD receipt Rachad deliberately
  left unmatched. Never turn zero `uncategorized` rows from that dataset into
  "no unmatched lines." Open banking reviews must inspect imported feed lines
  for each of the four active accounts directly; until that route is live-
  verified, the result is **inconclusive**, not zero.
- 2026-08-07: The dedicated authenticated Zoho UI session is live on the
  Canadian application domains `inventory.zohocloud.ca` and
  `books.zohocloud.ca`. The earlier connector falsely rejected those valid
  domains because it accepted only `.zoho.com`. `CONNECT_DADO_ZOHO_UI.bat` now
  opens a loopback-only live Edge session and `zoho_ui_session.py live-check`
  verified both applications without reading credentials or changing data.
  The dedicated Edge window must remain open while this UI access is used.
- 2026-08-07: The imported bank-feed route is now live-verified read-only:
  Zoho Books UI `GET /api/v3/banktransactions/uncategorized` returned HTTP 200.
  It exposed the deliberately unmatched Structural Composite Technologies
  receipt, transaction `96274000001534055`, CAD 4,101.30 dated 2026-08-07,
  under account `Chequing account (C)` (`96274000001409019`) with status and
  transaction type `uncategorized`. This account name/ID differs from the
  separately recorded active Desjardins CAD bank-account record; do not infer
  their relationship without further evidence. No Zoho write was performed.
- 2026-08-07: Inventory category access is blocked by the organization feature
  set, not by Dado's API scopes or user role. A read-only scan of all 38 loaded
  Inventory application modules found the real route `#/inventory/categories`,
  but the live FRP Depot session redirects it to `#/unauthorized`. The current
  Zoho user role is already `Admin`; the organization plan is
  `STANDARD_REVISED_2023 - 1 year`; its 68 enabled features contain no category
  feature. The subscription does report custom fields as supported. Therefore
  no additional API-console permission can enable categories on this plan.
  `zoho_inventory_category_tool.py` category writes remain hard-disabled and no
  category or item was changed.
- 2026-08-07: Rachad commissioned the named
  `zoho_inventory_classification_tool.py` as the replacement for unavailable
  Inventory Categories. Its write scope is limited to one item dropdown custom
  field named `Catalog Classification`, with exactly these choices: `Website
  Catalog`, `Custom / Customer-Specific`, and `Review / Unclassified`; and to
  assigning that field on existing items. Every write must use immutable
  stage-then-commit plans and Rachad's own one-word `APPROVED` response to the
  displayed plan. This commissioning authorizes building and testing refusal
  paths only; it is not approval of any live plan or Zoho write. The tool may
  not delete fields, add other choices, or change names, SKUs, prices, stock,
  accounts, item groups, or any other item property.
- 2026-08-08: Rachad commissioned one additional fixed operation in the named
  `zoho_inventory_item_tool.py`: rename only option ID `96274000000034781` on
  FRP FW PIPE group `96274000000034779`, attribute SIZE
  `96274000000023957`, from `30` to `30\"`. It is linked to items
  `96274000000034771` / `PIDN750150PSI411` and `96274000000034773` /
  `PIDN750150PSI470`. All group, attribute, option and item IDs; all other
  labels; and all names, SKUs, prices, stock, status, pressure and resin must be
  preserved. The fixed stage-then-commit plan requires Rachad's later one-word
  `APPROVED`; commissioning the capability is not approval of the staged plan.
  Rachad approved plan `20260808T164957Z_group_option_rename_9d6ba1d7.json` on
  2026-08-08. Zoho rejected its one PUT with HTTP 400 code 15: `Please ensure
  that the "attributes" has less than 100 characters.` Independent live GET
  verification proved the entire item-group state unchanged, including option
  value `30` and both linked items. The plan is permanently replay-locked; no
  retry or dependent WooCommerce Pipe staging occurred.
- 2026-08-08: Rachad commissioned a daily approval-gated Zoho banking review at
  08:15 Eastern/server time. It reads the imported feeds for the four logical
  accounts Desjardins CAD, Desjardins USD, Stripe, and PayPal; the CAD review
  checks both `Chequing account (C)` (`96274000001409019`) and `FRP Depots -
  Desjardins` (`96274000001411002`) so the differing live feed/account records
  cannot hide an open line. Clean runs are silent. Open lines are classified as
  customer receipt, payroll, expense, transfer, or unknown and reported with
  Zoho's current best candidate. The daily job performs no Zoho write and does
  not stage or commit. Every match/categorization still requires an exact named-
  tool plan shown to Rachad and his fresh one-word `APPROVED` reply.

## 2026-08-08 — Airwallex USD transfer API blocker (durable)
- Transfer `96274000001535012` remains live as source `FRPDepot Inc.` CAD
  (`96274000000097003`) to `USD Desjardins corporate build-up account` USD
  (`96274000001409012`), amount 21,642.71, currency CAD, exchange rate 1.
- The approved historical source should be `AWX_FRPDepot Inc._USD`
  (`96274000000149257`, USD, inactive). CAD transfer `96274000001533058`
  is already corrected separately.
- Zoho Books API rejects the USD source-account correction with HTTP 400,
  code 17004: "From and To accounts are in the same foreign currency. Please
  transfer funds in the same currency." This was live-proven both while
  preserving the stale CAD currency ID and while omitting currency/exchange-rate
  metadata. The live settings IDs are CAD `96274000000000087` and USD
  `96274000000000081`.
- The remaining likely API fix requires explicitly changing the transfer's
  currency to USD. That is outside the commissioned banking tool's account-link-
  only correction scope, so Dado must not stage or commit another API variant.
  Resolution requires Rachad to edit the transfer manually in Zoho, or a separate
  explicit expansion of the commissioned write scope. All failed plans are
  permanently replay-locked; the live transaction was verified unchanged.

- 2026-08-08: Rachad commissioned a narrowly restricted image-alt-only extension
  to the named WooCommerce catalog-change tool. It applies only to existing
  product updates and requires the complete live image gallery with the same IDs
  and order; each payload image contains exactly `id` and nonblank plain-text
  `alt`. The tool refuses image creation, removal, replacement, reordering,
  URL/file/metadata changes, product creation, and variation images. Every alt
  write remains an immutable staged plan shown to Rachad, his one-word
  `APPROVED`, one commit attempt, replay locking, and complete-gallery live
  readback. The complete safety suite passes 48/48.
- 2026-08-09: Rachad chose the WooCommerce shipping-policy direction: all FRP
  Pipe, Manway, Manway Cover, mixed shipments, and unknown/unverified items use
  **Freight Quote Required**; UPS is allowed only for small items whose actual
  one-piece packed dimensions and gross weight have been measured and
  independently verified. Fresh live classification covers 136 variations:
  58 freight-required now, and 78 Elbow/Stub Flange variations held from UPS
  pending 37 physical packing groups.
- 2026-08-10: Because FRP Depot is new and has no physical packaging records,
  Rachad authorized online research to improve the 37 Elbow/Stub Flange planning
  estimates. `packing_measurement_estimates_researched.csv` supersedes and
  withdraws the nominal-size-only v1. It uses FRP Depot's published radius,
  wall-thickness, stub-OD, 12-inch stub-length and OD-tolerance tables as primary
  evidence, independently corroborated by Litek/FRP Supply/FRP Fittings sources;
  all 78 WooCommerce variation identities and weights were live-confirmed
  read-only. Component dimensions are MEDIUM or MEDIUM-HIGH, but assumed packing
  material and unweighed packing allowance cap every row at MEDIUM overall.
  Thirty groups fall below UPS's published generic size/weight maxima on an
  estimate-only screen; seven exceed them (14–24-inch Elbows and 30/36-inch Stub
  Flanges). None is UPS-approved: UPS requires the actual packed extreme
  dimensions and a scale weight, so the active freight-only policy remains.
- 2026-08-10: The packing-experience collector is LIVE for future Elbow/Stub
  Flange orders. Its baseline is Woo order 2127; zero earlier orders were
  queued. `packing-order-monitor` (`89d95c692495`) reads a privacy-projected
  Woo order feed every 30 minutes and creates local opportunities only;
  `packing-observation-weekly-reminder` (`fd8eee0f604f`) runs Mondays at 09:00.
  Operational data stays outside the repo under
  `%LOCALAPPDATA%\FRPDepot-Packing-Observations\`. The collector stores no
  customer/address/payment/price data, cannot write WooCommerce or record a
  measurement automatically, and never changes estimates, UPS status, shipping
  classes or the checkout guard. Physical package data enters append-only only
  from Rachad's own words. Three distinct single-piece orders are required
  before an estimate recommendation is produced; every recommendation is
  review-only.
- 2026-08-09: Rachad commissioned the separate named
  `woocommerce_shipping_policy_tool.py`. Its write scope is limited to creating
  the one fixed class `Freight Quote Required` / `freight-quote-required`, and
  assigning or removing only that class on explicitly enumerated existing
  products/variations. Every immutable plan requires his later exact uppercase
  one-word `APPROVED`, is replay-locked before writing, and receives fresh-read
  and complete protected-field readback checks.
- 2026-08-09: Rachad confirmed there is no staging site and chose direct
  production testing. He approved and manually activated checkout-guard 1.0.0.
  It blocked checkout but displayed WooCommerce's generic cart-error sentence
  instead of `Contact us for a freight quote.`. Rachad immediately deactivated
  it. Fresh readback proved the plugin inactive, UPS enabled, and the storefront
  recovered. The failed plan and 1.0.0 ZIP SHA-256
  `4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25`
  are permanently closed and cannot be reused.
- 2026-08-09: The corrected checkout guard is version 1.0.1, with reproducible
  ZIP SHA-256
  `fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb`.
  The complete WooCommerce suite passes 248 tests with one skipped only because
  PHP is unavailable locally. Rachad commissioned the named
  `wordpress_plugin_deployment_tool.py` after requesting that Dado handle
  WordPress work through mobile approvals. It attaches only to Dado's dedicated
  authenticated WordPress UI session and may replace, activate, or deactivate
  only `FRP Depot Freight Checkout Guard`. It cannot delete plugins, touch any
  other plugin, change settings/content/users, or run generic browser actions.
  Every write requires an immutable 24-hour plan and his later exact uppercase
  one-word `APPROVED`; activation includes an anonymous live checkout test and
  automatic emergency deactivation on any failure.
- 2026-08-09: Live read-only inspection confirmed checkout-guard 1.0.0 installed
  and inactive. Rachad approved replacement plan
  `20260809T175228Z_plugin_replace_5431f3fc5a28e8a4.json`; the named deployment
  tool positively identified the fixed plugin and uploaded version 1.0.1, replaced
  it once, and fresh readback confirmed 1.0.1 installed and inactive. The plan is
  replay-locked. An initial unapproved activation plan was permanently abandoned
  before any write because its validation label said `1/2 inch` while the live
  product selector proved the exact option is `1/2"`. After correction and a
  fresh 248-test pass, activation plan
  `20260809T180243Z_plugin_activate_4841d651c0e89698.json` was staged locally.
  It targets the live `1/2"` / `150PSI` / `D411` variation, requires exactly one
  `Contact us for a freight quote.` notice with checkout/payment blocked, and
  automatically deactivates on any failure. That plan later failed closed and is
  replay-locked.
- 2026-08-09: Two repaired activation attempts were safely rolled back before
  validation: the first timed out and the second proved the validator was forcing
  hidden selects, leaving Add to cart disabled. Both plans are permanently closed.
  `wordpress_plugin_deployment_tool.py` 1.2.0 now clicks the measured visible
  `role=radio` customer controls and requires WooCommerce to resolve the variation
  and enable Add to cart. The complete WooCommerce suite passed 388 tests with one
  PHP-only skip. Three independent read-only preflights then passed with evidence
  SHA-256 `c6027bd526f7f57c839f70f893372cf33f86e68573f0609e9064164e28da4346`.
  Rachad approved activation plan
  `20260809T193552Z_plugin_activate_1d238c626000dc39.json` (SHA-256
  `1d238c626000dc397f31781ab0c60015f2c33bf213fd3eab680c1a14fe912fd2`).
  The plan committed successfully: visible controls were selected, the variation
  resolved, Add to cart enabled, exactly one `Contact us for a freight quote.`
  notice appeared, checkout/payment were blocked, and no order was placed or UPS
  setting touched. Independent WordPress readback confirmed version 1.0.1 active
  with fingerprint `6699faaffa9d395f287c1bf0f8a52be5b10092a2130fcd66832fbb160e6ab129`.
  The successful plan is replay-locked.
- 2026-08-09: Rachad approved WooCommerce shipping-class creation plan
  `20260809T194335Z_shipping_class_create_3ebdc6b9cc50e3fe.json` (SHA-256
  `3ebdc6b9cc50e3fe735c3a1f822d4b820912a8b49e7014ff703649ca35967d34`).
  The commissioned shipping-policy tool created and read back exactly one class:
  ID 61, name `Freight Quote Required`, slug `freight-quote-required`. The plan is
  replay-locked. No product/variation assignment or UPS change was included.
- 2026-08-09: Rachad batch-approved three freight-class assignment plans. The
  first Pipe plan stopped after its first target because WooCommerce changed a
  protected field during the shipping-class update. The plan is permanently
  indeterminate/replay-locked. Read-only reconciliation proved exactly one live
  assignment landed: Pipe variation 1456 / SKU `PIDN25150PSI411` now has
  `freight-quote-required`; its current protected state is stable across two fresh
  reads but differs from the staging fingerprint. The remaining 33 Pipe targets
  are blank. The 13 Manway and 11 Manway Cover plans were never started and no
  targets from either family changed. The per-field verifier was then built and
  passed the full WooCommerce suite (438 run, 437 passed, one PHP-only skip,
  zero failed). Rachad approved one schema-2 diagnostic assignment for Pipe
  variation 2056 / SKU `PIDN12150PSI411`. The intended freight class landed,
  but the plan locked indeterminate because exactly one metadata value changed:
  `_wc_gla_sync_status` (same entry ID/key/order; no add/remove), from `synced`
  to `pending` immediately after the save. Two later fresh GETs proved it had
  automatically returned to `synced` and was stable; no other protected field
  changed. Current reconciled Pipe state: variations 1456 and 2056 assigned,
  32 blank. The diagnostic plan remains permanently locked and is never replayed.
  Before more assignments, add only a bounded read-only convergence check for
  this exact Google Listings transition; never accept `pending` as the final
  state and never relax any other protected-field check.
- 2026-08-08: Rachad said the standard sizes live in his Google Drive, in the
  **FRP Depots** folder, in one of the Excel files there (Telegram 23:48; the
  logged message is truncated mid-word at "in one of the exce"). This is the
  pointer to search, NOT a confirmed filename or a size list — read the actual
  workbook and cite it before stating any size.
- 2026-08-10: `zoho_email_template_tool.py` BUILT, TESTED and STAGED read-only.
  Motivation: Zoho Books ANDROID exposes neither Android contacts nor SwiftKey
  clipboard clips in its CC picker, so the fix is org-wide invoice email
  templates carrying preset CCs. Live read-only discovery established the real
  surface — settings route
  `#/settings/emails/templates?email_type=invoice_notification`, and readable
  endpoints `GET /api/v3/settings/emailtemplates` and
  `GET /api/v3/settings/emailtemplates/{id}`. There is EXACTLY ONE live
  `invoice_notification` template, `Default`, ID `96274000000000014`,
  `is_default` true, with `cc_mail_ids` EMPTY and `bcc_mail_ids` EMPTY —
  clone fingerprint
  `4a19c02d41e5ba90349345269bde0b056259e4a5e9f0144f7bc55ee4d281886e`.
  NO TEMPLATE WAS CREATED AND NO EMAIL WAS SENT. Zero Zoho writes; the encrypted
  vault and profile .env were not touched.
- 2026-08-10 (same day, later): the native Save contract WAS captured with
  Rachad's authorization, and it moved the blocker rather than clearing it.
  Tool now v1.1.0 / schema 2, so every plan staged under the old blocker fails
  closed. Tests: 103 in the email-template suite, 421 across the whole Zoho
  suite, all passing. Fresh read-only plan
  `20260810T231950Z_create_accounting_test_2fa2a591d4936ffe.json` (SHA-256
  `2fa2a591d4936ffe3482deb9870240ed909f859bd2147e5d5f27d07c0a665a46`) is staged
  and creates `CC - Accounting` ONLY — but see the blocker; approving it refuses
  before any write.
  WHAT THE CAPTURE PROVED: opening the fixed `New` form under an
  abort-everything interceptor, filling only the fixed name and the fixed Cc
  option, and clicking Save emitted exactly ONE request — `POST` to
  `https://books.zohocloud.ca/api/v3/settings/emailtemplates`, empty query, form
  body `JSONString=` + `organization_id`, body SHA-256
  `850b177880f00f693bca3ee367a8f548b06bf252e9db873c32a591233c29b7ad`, aborted
  before the network. Payload schema is closed: 7 top-level keys, and exactly
  one `language_content` block with `subject`/`body`/`language_code=en`/
  `is_default=true`. No header, cookie, storage value, CSRF token or password
  was read.
  *** THE REAL BLOCKER — CONTENT, NOT CONTRACT. *** Decoding that body proved
  Zoho's `New` form does NOT clone this org's `Default`. It loads Zoho's stock
  factory invoice body: stock reads BALANCE DUE / %Balance% / MAKE PAYMENT /
  Regards %UserName% %CompanyName%, while the live `Default` reads INVOICE
  AMOUNT / %Total% / PAY NOW / Regards Accounting Departement (subject and From
  DO match; bodies differ, 2118 vs 2131 chars). The commission requires targets
  to preserve `Default`'s body, so creating through `New` would either break
  that guarantee or succeed at the POST and then fail this tool's own
  clone-fidelity read-back — leaving an ORPHAN template behind a permanently
  locked plan. `require_native_form_clones_source` therefore refuses BEFORE the
  replay lock, comparing the live Default's body against what the form actually
  emitted.
  NEXT STEP, Rachad's call, two options: (1) a native `Clone` control DOES exist
  on the `Default` row — its menu holds exactly `Edit` and `Clone`, verified
  read-only with one click on the disclosure toggle and zero write requests — so
  authorize a blocked capture of the Clone form's single Save request the way the
  New form's was captured; or (2) say plainly that the stock body is acceptable,
  which changes what customers receive and is his decision, not Dado's.
- 2026-08-10 21:56 EDT live queue verification: the saved Zoho connection now
  includes the named restricted existing-invoice revision and draft-invoice
  creation access; `zoho_tool.py check` verified Books and Inventory. This
  supersedes the earlier pending-OAuth statements for those two capabilities.
  No SHM invoice-revision plan is staged: SHM Marine Constructors JV is still
  missing as a Zoho customer, and tax treatment still needs delivery/carrier
  facts.
- 2026-08-10 21:56 EDT live WooCommerce GET audit of the 58 intended protected
  variations found only 2 Pipe variations assigned to `freight-quote-required`;
  32 Pipe, 13 Manway, and 11 Manway Cover variations remain blank. No unexpected
  class or missing target was found and the audit performed zero writes. The
  authenticated WordPress admin window was closed, so the plugin's last verified
  state remains version 1.0.1 active, but current activity could not be freshly
  confirmed.
- 2026-08-10 21:56 EDT live Zoho Books lookup: Airwallex USD transfer ID
  `96274000001535012` now returns `Transaction does not exist`, the imported-feed
  fallback has no row, and a complete Status.All ledger read for 2026-07-20
  through 2026-07-31 has no transaction for USD 21,642.71. Do not reuse the old
  transfer-correction plans or claim the old wrong source link still exists;
  investigate whether Rachad manually removed/recreated it or Zoho moved it.
- 2026-08-10 21:57 EDT Rachad answered that question himself: "Do the airwallex
  correction. I un categorized it for you". He uncategorized the imported line,
  which is why transfer `96274000001535012` is gone. The USD 21,642.71 line is
  back to imported statement line `96274000001423074`, status `uncategorized`,
  dated 2026-07-23, USD (currency ID `96274000000000081`), bank charges 0,
  description `Funds transfer received /FRPDepot Inc. /`, on the destination
  account `96274000001409012`. That instruction authorizes build/test/stage only;
  it is NOT approval to commit.
- 2026-08-10: `zoho_banking_reconciliation_tool.py` gained ONE fail-closed
  recovery path inside its ALREADY COMMISSIONED `categorize` action. Not a new
  capability: categorizing an imported line as an internal transfer was already
  commissioned. It only replaces the ordinary Airwallex guard's demand for a LIVE
  outgoing counterpart -- which uncategorization made permanently unsatisfiable --
  with the immutable replay-locked plan
  `20260808T031444Z_update_transfer_accounts_973274b986060804.json`
  (sha256 `973274b986...3a41d908`, lock status `indeterminate`, `no_retry` true),
  whose validated snapshot records this same imported line, amount, date,
  description and account mapping, PLUS a fresh live proof that transfer
  `96274000001535012` is really absent. Reachable only when EVERY fixed fact
  matches: statement ID, `uncategorized` status, 21642.71, 2026-07-23, USD +
  currency ID, description, zero bank charges, `transfer_fund` from
  `96274000000149257` (`AWX_FRPDepot Inc._USD`, may stay inactive) to
  `96274000001409012` (`USD Desjardins corporate build-up account`, must be
  active), reference `Closing Balance From Airwallex Account`. Requested by an
  explicit closed `recovery` block (mode + pinned historical digest + superseded
  ID), never inferred from text. EVERY other Airwallex categorization still
  requires its live outgoing counterpart; income/revenue types stay impossible;
  approval stays byte-exact `APPROVED`, one POST, lock before side effect, no
  retry, full fresh readback. Absence is proved by a distinct
  `BankingRecordAbsent` class, never a message match, so a failed read or a dead
  browser can never pass as "gone".
  TESTED, NOT YET STAGED: the focused banking suite passed 40/40 and the complete
  Zoho suite passed 488/488 on 2026-08-11. Live staging then stopped before plan
  creation or write because the dedicated authenticated Zoho Books UI window was
  closed (`Exactly one authenticated Canadian Zoho Books app page must be open`).
  The sanctioned UI session restart was launched as job
  `20260810T232210-3cb22d`; no categorization plan exists yet. ZERO Zoho writes,
  ZERO emails, and no approval has been requested for this banking correction.
  OPEN RISK to raise before he approves: Zoho rejected the earlier USD source
  correction with HTTP 400 code 17004, "From and To accounts are in the same
  foreign currency." Both accounts here are USD against a CAD base, so the
  categorize POST may fail the same way. That is a different endpoint and is
  untested; if it fails, the plan locks permanently and nothing is written.
- 2026-08-10: Rachad said "Repair the verifier for the website freight
  protection". THE DEFECT, and it is the durable fact here: schema 3 of
  `woocommerce_shipping_policy_tool.py` treated the SETTLED `_wc_gla_sync_status`
  value as the only lawful before-state and refused to stage anything else. That
  refusal deadlocks. Reproduced live 2026-08-10 on Pipe variation 1457 — staging
  refused before writing any plan because the entry held the pending digest
  `12adac...` rather than the required settled digest `bed425...`, and read-only
  monitoring had already recorded 1457 and the next five blank Pipe candidates
  sitting at that pending value long after the 90-second transient window. An
  untouched variation can rest there indefinitely, and schema 3 demanded a move
  before it would permit the update that causes the move.
  THE REPAIR, BUILT THIS SESSION: schema 4 / tool version 4.0.0. A closed baseline
  enum `absent` / `settled_baseline` / `pending_baseline`, decided by source from
  the value-free projection and RE-DERIVED from the plan's own hashed projection
  at load, so a rehashed edit cannot flip it. A pending before-state is staged
  only after a bounded READ-ONLY stability proof: the first read plus two more
  fresh GETs of the same exact resource on a fixed 2s/4s schedule (6-second
  ceiling) that must agree on the shipping class, `date_modified_gmt`, the
  aggregate and every per-field fingerprint, the complete metadata projection, and
  one sound entry at the exact pending digest with a stable numeric id — any
  disagreement refuses and writes no plan. Commit re-proves the whole baseline on
  a fresh read before its one PUT. After the write a pending baseline has exactly
  two successful shapes: the complete staged pending state unchanged (success
  IMMEDIATELY — it must never wait for settlement just to accept an unchanged
  state), or that same entry moving pending -> settled with same id/index/count/
  order and nothing else, which then needs a fixed 2s/4s confirmation showing the
  complete settled state stable. Anything else locks indeterminate; no retry, no
  rollback, no second PUT. The settled 90-second path is unchanged, no third digest
  exists, and schema 1/2/3 plans are refused before the vault and before the
  network even when rehashed. Baseline mode names are compound
  (`pending_baseline`, not `pending`) on purpose: the live value IS the word
  "pending", and a bare mode token in a plan would be indistinguishable from a leak.
  VERIFIED LIVE 2026-08-11: the first full execution surfaced two defects and
  they were repaired without weakening protection: malformed/absent bounded
  diagnostics now return false instead of raising, and the stale-preflight test
  now isolates commit-time sleeping from the required staging stability sleeps.
  The complete WooCommerce suite passed 618 tests with one expected PHP-only
  skip. A fresh schema-4 plan
  `20260811T032017Z_shipping_class_assign_2546a31f414cc049.json` was staged for
  Pipe variation 1457 only after three identical live pending-baseline
  observations; staging made zero store writes. Rachad then answered that exact
  website plan with his one-word approval. The one PUT committed successfully:
  variation 1457 / SKU `PIDN25150PSI470` now carries
  `freight-quote-required`; the complete protected state remained exactly at the
  staged pending baseline, so no convergence wait was needed. Fresh independent
  GET audit confirmed 3 of 58 intended variations assigned (Pipe 1456, 2056,
  1457), 55 blank, zero unexpected classes, zero missing targets. Plan SHA-256
  `2546a31f414cc049d5f737d5816aedda8b169c266c7b022d991dbad8a0d332ed`
  is `committed_verified` and replay-locked. No WordPress write and no email.
