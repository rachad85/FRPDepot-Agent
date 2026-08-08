# FRP Depot — company fit profile (Dado's fact sheet)

Dado: read this at the start of every session. Add facts here the
moment Rachad states them (dated). NEVER invent a fact not on this
sheet — ask instead.

## Company basics (Rachad to fill / Dado to capture as told)
- Legal name:
- What we sell: Catalog-based FRP products. Mailbox evidence includes FRP pipe,
  fittings, stub flanges, saddle tees, grating, profiles, and separately quoted
  coatings/unlisted items.
- Resin standard (Rachad, 2026-07-30): use DK411 unless Rachad explicitly requests another resin. Stock availability does not authorize changing the resin.
- Location / warehouse: Business/contact address repeatedly shown as 4507
  Ferguson Dr., Brockville, Ontario, Canada K6T 1A9. Warehouse status is not
  confirmed.
- Website / email domain: www.frpdepots.com / frpdepots.com.
- Rachad's FRP Depot email address: info@frpdepots.com (verified by Microsoft Graph)
- Outlook live-sweep rule: before declaring an RFQ open, fetch the full live conversation and verify the latest non-draft message is inbound, then cross-check Zoho Books transaction/payment status and Zoho Inventory price/stock data (learned 2026-07-23 after a false open-RFQ report).
- Official Outlook signature rule: every customer-facing draft must use the verified HTML signature extracted from a real Sent Item, including the inline FRP DEPOTS logo and all contact details; the bundle is `Dado/20_Working/outlook_signature/official_signature_bundle.json` (source Sent Item dated 2026-07-21, verified 2026-07-23).
- Outlook reply-thread rule (Rachad, 2026-07-23): every email reply draft must use **Reply All** from the latest live external non-draft message in the existing Outlook conversation—never a new standalone message. Preserve the existing subject, conversation identity, quoted history, and externally appropriate To/Cc roles; add the new reply above the history and the official HTML signature once. Check Drafts first, keep only one active response draft, then reopen it and verify the thread, recipients, body, signature, attachments, and that no newer source message arrived before reporting it ready.
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
- Currency: Verified transactions use CAD and USD. No single default currency is confirmed.
- Pricing currency (Rachad, 2026-08-07, approving the SCT coupling draft): "Prices
  are all in CAD". Stated over the stocked-coupling price comparison, so it governs
  the prices Dado presents and quotes. It does NOT restate the currency of a Zoho
  record or a supplier invoice — read those from the record itself.
- Payment terms default: No global default confirmed. Customer terms are account/order-specific; supplier terms are negotiated per PO/invoice.
- Shipping terms default: No global default confirmed. Observed orders use Ex Works, FCA, or customer-account collect arrangements.
- Quote validity default: No general default found in the mailbox; Rachad must confirm.
- Price list location / source of truth: Sales replies direct customers to the
  website catalog for listed pipe and fittings; exact source-of-truth rule still
  requires Rachad's confirmation.
- Stock availability rule (Rachad, 2026-07-30): use **Physical Available for Sale** only. Accounting stock and billed-but-unreceived purchase-order quantities must never be represented as physically in stock. Zoho's **Inventory Summary and phone quote item picker display accounting availability even when the organization mode is Physical Stock** (live-confirmed with SKU PIDN150150PSI411: 600 ft displayed versus physical 0 ft). For accurate quotes, open the item and use **Overview > Physical Stock > Available for Sale**, or use the read-only Inventory API field `actual_available_stock`.
- Sales tax follows the customer's address/jurisdiction. Troy Dualam Services Inc. (customer ID 96274000000060019) is in Quebec and must use the combined **GST + QST** tax group (14.975%; Zoho tax ID 96274000001071139).
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
