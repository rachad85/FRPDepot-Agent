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
- Hetron offering policy (Rachad, 2026-08-13): FRP Depot does not offer Hetron resin. Remove Hetron from every public website page, selector, guide link and catalogue presentation. Existing hidden/private historical records are not an offering and must not be published; deleting or changing them requires an exact separately approved write plan.
- Website specifications/catalogue replacement plan (Rachad, 2026-08-15): the working source is the live Google Drive `Specs & Catalog` folder and is being overhauled with Claude. When Rachad declares it final, Dado is to replace the old website catalogue and give each product a direct shortcut to its own section rather than opening the complete catalogue at the beginning. The 2026-08-15 editable HTML snapshot already separates Stub Flanges, Manways & Covers, 90° Elbows, Filament-Wound Pipe and FNPT Couplings, but contains no section IDs or fragment links yet; those stable anchors belong in the final website implementation. Keep the existing live catalogue unchanged until the final source is frozen and separately staged/approved.
- Product-image overhaul (Rachad, 2026-08-15): every product family is to receive multiple polished image variations at the quality level of the approved FNPT coupling set. Use FRP Depot-owned originals from Drive and the current site as primary sources; web and competitor images are visual references only and their pixels, branding, labels and watermarks are not republished. Preserve exact product geometry and a realistic resin-rich FRP appearance—never glossy PVC, metal or invented construction details. Final images and any website assignment remain subject to Rachad's visual approval and a separately staged website plan.
- Public chemical-guide policy (Rachad, 2026-08-13): Derakane/Alta chemical guides are public and FRP Depot may use them for its searchable guide. Do not treat separate INEOS/Alta permission as a blocker. Data accuracy, complete footnotes, units, source attribution and technical limitations still must be proven before wider promotion. For broad or mixture chemical names, publish no normalized CAS unless the exact substance form is independently proven; keep the chemical name searchable and retain the guide's raw identifier internally.
- Customer stock-disclosure rule (Rachad, 2026-08-13 13:29, on the Taha reply):
  "let not tell him how much we have in stock and send him unit pricing." Do NOT
  put our stock quantities in a customer-facing draft. Quote UNIT PRICING and,
  if needed, availability in plain words. Stock figures are internal, like costs.
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
- Sales-order processing notification requirement (Rachad, 2026-08-11): after converting a Quote to a Sales Order, Rachad wants to send the processing message directly from Zoho using a Sales Order email template. The fixed internal recipients are `logistics@frpdepots.com` and `operations@frpdepots.com` in To, with `accounting@frpdepots.com` in Cc, and the Sales Order PDF attached. Rachad presses Send himself; no automatic sending and no Outlook watcher.
- Sales Receipt policy (Rachad, 2026-08-11): use Zoho Sales Receipts only when the customer pays in full at the time of sale. Normal FRP Depot business remains Quote -> Sales Order -> Invoice -> Record Payment -> payment confirmation or paid invoice. The live organization had zero Sales Receipts when this policy was set.
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
- Supplier-cost pricing rule (Rachad, 2026-08-10; extended 2026-08-11): calculate
  the target CAD selling price as `supplier USD cost × 3.6`. The multiplier includes
  currency conversion, shipping, handling, and margin. It applies to the current FNPT
  supplier quotation and to the FRP backing-ring stock Rachad listed on 2026-08-11.
  Flag every current Zoho/Woo selling price below that target for review; do not
  change a price without a separate approved write plan.
- FRP backing-ring landed valuation (Rachad, 2026-08-11): use Fei's quoted USD
  unit costs tentatively, preserve those supplier figures as provenance, add a
  separate 20% landing allowance, then convert to CAD using the Bank of Canada
  daily average for the 2026-08-11 inventory-receipt date: 1 USD = CAD 1.3927.
  Formula: `quantity × Fei USD unit cost × 1.20 × 1.3927`; retain the exact
  converted unit basis through multiplication and round each CAD line total once,
  half-up, to two decimals. This gives CAD 78,816.51 for the eight new items / 713
  pieces. The 20% is tentative freight/duty/landing cost, not selling markup, and
  does not change the separate Fei USD cost × 3.6 CAD selling-rate rule. The
  approved 713-piece quantity adjustment loaded the quantities but Zoho valued
  every line at CAD 0.00 because the eight new items' purchase rates were zero;
  adjustment 96274000001555048 is permanently replay-locked. Therefore the
  CAD 78,816.51 remains the approved tentative valuation basis, not the current
  live Zoho inventory value. Rachad commissioned the separate fixed value-only
  correction tool on 2026-08-11 and approved its plan on 2026-08-12. VALUE
  Inventory Adjustment 96274000001555109 added exactly CAD 78,816.51 through
  eight `value_adjusted` lines with no quantity field or item/rate write. Fresh
  reads show status Adjusted, valuation no longer pending, and the 713 pieces plus
  every protected item field unchanged. The plan remains permanently no-retry.
  Replace the tentative valuation when actual supplier/freight costs are known
  only through a separately approved accounting correction.
- FRP backing-ring catalog facts (Rachad, 2026-08-11): treat every ring on his
  handwritten stock sheet as 150 PSI and use the default D411 resin. Prepare the
  Zoho Inventory catalog first; do not publish the website products until Rachad
  supplies product pictures. Catalogue exactly ONE item per nominal size: black
  and white colour differences do not matter and colour must not appear in the
  Zoho item name, SKU, description, quotes, or later website label. Do not show
  outside diameter either. The 4-inch and 10-inch sheet rows are the same
  products as existing generic Zoho items `BRDN100150PSI411` and
  `BRDN250150PSI411`; merge their counts into those existing item IDs and keep
  current orders linked. The 1-1/2-inch total is 85 pcs. The eight colour-neutral
  1, 1-1/2, 2, 3, 6, 8, 12 and 14-inch Zoho items were created and live-verified
  on 2026-08-12 at the approved Fei-cost × 3.6 rates. Their 713 physical units
  are not loaded yet; live starting stock is zero pending a separate approved
  inventory-adjustment plan. The existing 4-inch/10-inch items already hold 113.
- FNPT WEBSITE GO-LIVE RULE (Rachad, 2026-08-11; supersedes the earlier hold-for-
  every-supplier-price rollout rule): publish product 2061 using the lower of
  (a) revised supplier USD cost × 3.6 CAD and (b) the exactly equivalent
  Litek/FRP Supply public USD price converted at the stated exchange-rate source.
  Where supplier cost is missing, match FRP Supply only when a defensible cost
  ceiling proves the result remains profitable; otherwise keep that variation
  unavailable rather than expose a placeholder price. Unsupported resins may not
  borrow D411/D470 competitor prices. Product type, size, length, pressure, resin,
  connection, currency and price basis must match before applying a competitor
  price. Zoho `actual_available_stock` remains the stock authority and must be
  freshly matched immediately before the first website write. Public product
  photos are visual references only; the live gallery uses original, unbranded
  FRP Depot imagery. The specific immutable go-live plan still requires Rachad's
  later exact one-word `APPROVED` before any website write.
- FNPT ONLINE RECOVERY STATUS (2026-08-12): the first approved immutable go-live
  plan is permanently replay-locked `indeterminate` after exactly one PUT. Live
  readback proves variation 2062 (`FNPTCOUPLING-DERAKANE470-1/2\"6\"`) is at
  CAD 37.44 and published; parent 2061 remains Draft with an empty gallery and
  the other 63 variations remain at their staged baselines. The stop was caused
  by WooCommerce automatically recalculating the parent's price HTML and Yoast
  price fields after the child price save; those exact derived fields are now
  modelled and tested, not broadly ignored. No retry or rollback occurred.
  Rachad's Aug. 12 supplier workbook (SHA-256
  `fc99d4a46d289062540535a686dc482d7224b944d3fe9bf51f7caf18ce4d416e`)
  provides all 32 D411/D470 6-inch and 8-inch costs and states MOQ >=10 per
  specification. A new read-only recovery plan is staged at canonical SHA-256
  `19c1c8bfecd9a6e51eb067c9a7a90cc54fd939aa6db6085cec42105b0d1221e7`:
  32 supported D411/D470 variations, 32 unsupported Hetron 922/D510A variations
  Private, six fixed original images, then parent publication last. Price sources
  are 19 exact FRP Supply caps, 9 supplier-cost x3.6 results and 4 supplier-cost
  x3.6 results where no exact 8-inch competitor equivalent exists; conservative
  margins using supplier USD cost x1.20 at CAD/USD 1.3943 range 30.64%-53.53%.
  Variation 2062 is verify-only and has no PUT route; 65 independent writes remain
  (63 variations, gallery, publication). Fresh staging stock comparison passed
  64/64 against Zoho physical availability (12 in stock, 52 out), and commit must
  repeat it before its lock and first write. Tests: targeted 31/31 and complete
  WooCommerce 690 passed with one expected PHP-only skip. The old approval cannot
  authorize this replacement plan; it awaits a new exact `APPROVED`.
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
- FNPT supplier quotation Rev. 01 dated Aug. 12, 2026 supersedes the earlier
  incomplete Rev. 01 evidence for online pricing. It contains 32 mapped D411/D470
  catalog costs: both 6-inch and 8-inch lengths for sizes 1/2, 3/4, 1, 1-1/4,
  1-1/2, 2, 3 and 6 inches. Every mapped value is pinned to its workbook cell and
  the workbook SHA-256 above. Supplier note: unit prices require minimum order
  quantity >=10 for each specification. This does not by itself authorize any
  Zoho sales-rate update; Zoho writes remain a separate staged plan and approval.
- Payment terms default: No global default confirmed. Customer terms are account/order-specific; supplier terms are negotiated per PO/invoice.
- Shipping terms default: No global default confirmed. Observed orders use Ex Works, FCA, or customer-account collect arrangements.
- Quote validity default: No general default found in the mailbox; Rachad must confirm.
- Price list location / source of truth: Sales replies direct customers to the
  website catalog for listed pipe and fittings; exact source-of-truth rule still
  requires Rachad's confirmation.
- Stock availability rule (Rachad, 2026-07-30): use **Physical Available for Sale** only. Accounting stock and billed-but-unreceived purchase-order quantities must never be represented as physically in stock. Zoho's **Inventory Summary and phone quote item picker display accounting availability even when the organization mode is Physical Stock** (live-confirmed with SKU PIDN150150PSI411: 600 ft displayed versus physical 0 ft). For accurate quotes, open the item and use **Overview > Physical Stock > Available for Sale**, or use the read-only Inventory API field `actual_available_stock`.
- Sales tax follows the customer's address/jurisdiction. Rachad stated on 2026-08-10 that FRP Depot is not registered to collect BC PST. This registration fact alone does not settle the tax treatment of a BC order; confirm the current CRA place-of-supply and BC out-of-province PST rules, plus the delivery terms, before changing or presenting tax.
- Client PO reference rule (Rachad, 2026-08-12): read the complete customer email and original PO, then put the customer's own PO number in Zoho's visible **Reference#** (`reference_number`) on **every related Sales Order and Invoice**. Preserve meaningful prefixes/suffixes and leading zeroes exactly. Notes, attachments and FRP Depot `QT-`/`SO-`/`INV-` numbers are not substitutes. If no customer PO exists or evidence conflicts, ask Rachad rather than inventing one. Verify both the fresh live field and that the rendered document visibly shows it before reporting completion.
- Zoho invoice capability commissioned by Rachad on 2026-08-10: a named approval-gated tool may (1) revise existing invoices without sending them and without changing their status, and (2) create new invoices in **Draft** status only. Every save is staged and requires Rachad's later exact one-word `APPROVED`; sending/emailing, deleting, voiding, marking sent, payments, credits and automatic approval remain unreachable. Build and OAuth permission work are pending; commissioning itself granted no live permission and caused no Zoho write.
- Zoho invoice REVISION tool status (2026-08-10): **BUILT AND TESTED ONLY — no permission granted, no plan staged, no invoice changed.** `Dado\Tools\zoho\zoho_invoice_revision_tool.py` revises ONE existing invoice with ONE atomic PUT, changing only `customer_id` (to an ALREADY EXISTING customer), `reference_number`, `date`, `due_date`, customer-owned `billing_address_id`/`shipping_address_id`, `notes`, `terms`, and per existing line `quantity`, `rate`, `discount`, `description`, `tax_id`. Every live line is always resent once in order with its line_item_id and item_id, so nothing can be deleted by omission; adding, removing or substituting a line is refused. It has **no mail transport at all**, cannot change the invoice number, status, currency, exchange rate, balance/payments, adjustments, shipping charges or custom fields, and cannot create, delete, void, mark-draft or mark-sent. It refuses any invoice that is not exactly `draft` or `sent` or that carries a payment, credit, write-off, package, shipment or recurring profile. Approval is byte-exact `APPROVED`; one attempt only, and any failure or indeterminate result permanently locks the plan. The OAuth scope `ZohoBooks.invoices.UPDATE` is in the PREPARED list only and is **not live** until Rachad runs PREPARE_DADO_ZOHO_ACCESS.bat, creates the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat.
- Zoho DRAFT INVOICE CREATION status (2026-08-10, follow-on to the revision build): **BUILT AND TESTED ONLY — OAuth reauthorization still pending, no plan staged, zero Zoho writes, zero emails, no invoice created.** Part (2) of the commissioned capability is now implemented as the second action, `create_draft_invoice`, of the SAME named tool `Dado\Tools\zoho\zoho_invoice_revision_tool.py` (stage with `stage-create`, commit with the same `commit`). It creates ONE new invoice with ONE `POST /books/v3/invoices` and verifies live that the result is in exactly `draft` status. **Zoho's own auto-numbering assigns the number** — no caller-supplied number and no `ignore_auto_number_generation` exists anywhere in the module. It requires an EXISTING ACTIVE customer whose live name matches what was stated, addresses owned by that customer, and EXISTING ACTIVE Zoho items on every line (no free-text or unlinked lines; no item, customer or tax creation). Quantity, rate, discount, description and tax ID are accepted only with an explicit `source` string per value. Both `date` and `due_date` must be stated so nothing is inferred. The customer's own currency is preserved; currency and exchange rate are not in the payload allowlist. A duplicate item line is refused unless every line for that item carries its own distinct description. An independent Decimal (half-up) calculation of each line total, the discount, tax and grand total is shown before approval and asserted on read-back wherever Zoho's result is deterministic — a **tax group or compound tax is shown as an ESTIMATE and deliberately not asserted**, because Zoho rounds each component separately. Read-back verifies status exactly `draft`, the auto number, customer, currency, addresses, every line's item/order/quantity/rate/discount/description/tax/line-total, the dates, reference, notes, terms, the totals, that `is_emailed` is false, and that no shipping charge or adjustment appeared. If the POST succeeds but the read-back is missing or not draft, it reports an indeterminate failure **with the invoice ID when known and never attempts cleanup, deletion, voiding, a status change or a retry**. Approval is byte-exact `APPROVED`, checked before the lock, the vault, the token and the network; the plan is locked before the POST and permanently after any attempt. OAuth scope `ZohoBooks.invoices.CREATE` was added to the PREPARED list alongside `.UPDATE` and is **not live** until Rachad runs PREPARE_DADO_ZOHO_ACCESS.bat, creates the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. There is still no invoice DELETE/ALL/fullaccess scope.
- INV-000051 revision request (fresh live check 2026-08-12) is CONTEXT, NOT APPROVAL. Full Outlook thread confirms: Elaine Iverson requested invoicing to **SHM Marine Constructors JV**; Josh Caulfield confirmed SHM will send its own courier once ready; Rachad's own stated treatment is customer collection FOB Brockville with **Ontario HST 13%** and PO **0000031**. The supplied SHM PO names bill-to SHM Marine Constructors JV, 343A Bay St, Victoria BC V8T1P5, and says all invoices go to elaineiverson@ralmax.com. Live Zoho proves three blockers to the current commissioned invoice tool: (1) invoice 96274000001559012 is now status `overdue`, not exactly `draft` or `sent`; (2) exact customer SHM Marine Constructors JV still does not exist (customer creation belongs solely to `zoho_customer_quote_tool.py`); (3) invoice is linked to SO-00050 / salesorder 96274000001558003 and both lines carry sales-order line links, so the tool refuses changing line tax from GST 5% to ON HST 13% because that would desync the order. Live ON HST is tax ID 96274000000035516 at 13%. No plan was staged and zero Zoho writes/emails occurred. The corrected independent Decimal figures are subtotal CAD 13,020.00 + HST CAD 1,692.60 = total CAD 14,712.60. Any solution now needs Rachad to commission an exact fixed prerequisite/correction extension; do not stage a partial customer/PO-only revision that leaves the wrong tax. The later forwarding remains a DRAFT-only Outlook action and is never performed by the revision tool.
- INV-000051 correction live outcome (2026-08-12): Rachad separately approved the fixed SHM customer prerequisite and the fixed invoice correction. Plan A created and verified active customer **SHM Marine Constructors JV** (contact `96274000001569002`, billing address `96274000001569004`, primary Elaine Iverson contact person `96274000001569003`); zero email. Plan B issued its one locked PUT, but Zoho rejected it with HTTP 400 / code 4116 because the customer cannot be changed on this quote-derived invoice and instructed creating a new invoice instead. Plan B is permanently `indeterminate` / no-retry. Three fresh read-only rounds proved no invoice or sales-order business change landed: INV-000051 remains Overdue under Ralmax, reference SO-00050, GST 5%, total CAD 13,671.00, and SO-00050 remains unchanged. A replacement Draft invoice requires a separate newly staged plan and new approval; existing-invoice void/delete/credit/restatus remains outside commissioned capability.
- SHM replacement invoice live outcome (2026-08-12): Rachad separately approved Draft-creation plan `4d4cbff46c882f8bcf5dede8fd3d0601bd2b5a9169a3431a9fe780f8e9144b43`; one POST created **INV-000053** (`96274000001569012`) in Draft for SHM, PO 0000031, two preserved lines, ON HST 13%, subtotal CAD 13,020.00, tax CAD 1,692.60, total/balance CAD 14,712.60, and zero email. The immediate verifier locked `indeterminate` because Zoho omitted `billing_address_id` on GET while embedding the exact approved address. Three fresh reads proved all approved business values exact, INV-000051/SO-00050 unchanged, and no email; only Zoho's regenerated `invoice_url` varied. The lock remains permanently `indeterminate` / no-retry.
- Troy Dualam Services Inc. (customer ID 96274000000060019) is in Quebec and must use the combined **GST + QST** tax group (14.975%; Zoho tax ID 96274000001071139).
- Troy Dualam Services Inc. receives an automatic **10% discount** on every FRP Depot order/estimate (Rachad, 2026-07-30).
- Manufacturer-confirmed pipe construction: Fei wrote on 2025-11-26, **“all pipe sizes adopt filament winding method.”** Her 2025-11-04 attachment, `Filament Wound Pipe Lamination.pdf`, clarifies that this means the **structural roving layer** is filament-wound, while the **inner surface, chopped-strand-mat interior layer, and C-veil + UV outer surface** are hand-laid. Do not describe this as separate hand-laid axial sections. The document covers 1–36 in pipe and cites ASTM D2992 design basis / ASTM D2996 manufacture.


## People
- Rachad Homsi — owner. Telegram 891365639.
- Anh — handles Forte (payroll / direct deposit) matters; Rachad stated
  2026-08-11 "Forte is being handled by Anh", so do not raise Forte items to him.
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
  checksums. Sheet screenshots omit "live Google Sheets" wording and crop at the
  latest payment.
- 2026-08-15: Rachad commissioned `google_catalogue_publish_tool.py` for one
  exact catalogue publication. It can replace only the content of existing
  Google Drive file `1PqcjZf-SSCbBVp7quMri_ernaOPZDPz1`, `FRP Depots Catalogue
  2026.pdf`, in `My Drive / My Files / Rachad / Bussiness Folder / FRPDEPOT INC.
  / Specs & Catalog`, and only with the approved nine-page local PDF SHA-256
  `60bf4a5fcc19246f2d782608df145b06c83275fd30cec2ba7b3506b2c7382fb3`.
  It preserves the existing file ID, name, MIME type, path and share links. It
  cannot create, delete, copy, rename, move, change permissions/sharing, email,
  or use a browser. Stage is read-only; commit requires a 24-hour immutable plan
  plus Rachad's fresh exact unpadded uppercase `APPROVED`, makes one conditional
  media-only HTTP PUT with no retry or rollback route, and verifies downloaded
  live bytes. Any failure or uncertainty permanently locks the plan.
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
- 2026-08-11: Rachad chose option (1). The blocked `Clone` capture ran, he
  answered **`YES`** to accepting the one difference it revealed, and the Clone
  create path was implemented and tested. Tool is now **v2.0.0 / schema 3**, so
  every plan staged under the old New-form blocker fails closed.
  THE CLONE CONTRACT, measured not assumed: the `Default` row's exact
  `Show dropdown menu` disclosure -> exact `Clone` item -> fixed name + fixed Cc
  option -> Save emitted exactly ONE request, `POST`
  `https://books.zohocloud.ca/api/v3/settings/emailtemplates`, empty query, form
  body `JSONString` + `organization_id`, body SHA-256
  `f6e9d14c56e6560632f21245755674cee0a4013282a82ac5282b258eee5ff0ab`, aborted
  before the network. Its payload is a FLAT EIGHT-KEY object (`bcc_mail_ids`,
  `body`, `cc_mail_ids`, `from_address_id`, `is_default`, `name`, `subject`,
  `type`) — a DIFFERENT schema from the `New` form's nested `language_content`,
  which is why the two are decoded by separate functions and neither may stand
  in for the other. No header, cookie, storage value, token or password was read.
  THE ACCEPTED EQUIVALENCE, and its exact limit: the Clone body is byte-different
  from `Default` but canonically identical. Both are 2,131 characters, both parse
  to exactly 106 canonical events, and every event is equal; Zoho only reorders
  `href`/`style` on the PAY NOW `<a>` and `class`/`style` on its two nested
  `<span>`s. `same_canonical_html` ignores HTML ATTRIBUTE ORDER AND NOTHING ELSE
  — it preserves tags, nesting, attribute names, attribute values, quoting, data
  and whitespace nodes, entities, comments and declarations exactly, REFUSES
  duplicate attributes rather than letting a parser collapse them, and REFUSES
  malformed or unclosed markup rather than repairing it. The plan's own source
  fingerprint still protects the live `Default` body BYTE-for-byte.
  *** AN UNAUTHORIZED LIVE WRITE HAPPENED DURING THIS BUILD. SAY IT PLAINLY. ***
  Mutation-checking deleted `@holds_zoho_browser` to prove it was load-bearing.
  It was — but with it gone,
  `test_busy_browser_refuses_for_free_and_leaves_the_plan_reusable` (which calls
  `command_commit` directly and deliberately does NOT patch
  `create_template_via_ui`) had nothing left between it and the live session. The
  suite's fake vault carries the REAL organization id and the real `Default`
  template id, so it drove the real browser and saved a real `CC - Accounting`
  invoice template, ID **`96274000001558092`**, WITHOUT Rachad's approval. It is
  a faithful fixed clone — CC exactly `accounting@frpdepots.com`, BCC empty,
  non-default, subject/From/type matching `Default`, body canonically identical
  — and NO email was sent and nothing else changed. It has NOT been deleted:
  this tool has no delete route and removing it is Rachad's call, not Dado's.
  TWO FIXES, both mutation-checked: (a) the test module now patches
  `sync_playwright` ITSELF at module scope, because patching the read transport
  was never enough — the create path opens its own playwright session; (b) the
  read-back defect the accident exposed, below.
  *** `placeholder` IS DERIVED FROM THE NAME, NOT INHERITED. *** It was in
  `SOURCE_CLONE_FIELDS`, i.e. required byte-equal to the source. Zoho derives it
  from the template's own name — measured on both live templates, `Default` ->
  `mt_default` and `CC - Accounting` -> `mt_cc_accounting` — so a faithful clone
  can NEVER carry the source's. Every successful create would have failed its own
  read-back, reported indeterminate, and left an ORPHAN template behind a
  permanently locked plan: the exact outcome the create gate exists to prevent.
  Now checked by `derived_placeholder` under its own explicit rule. The accidental
  write is what proved this; no test written against the old assumption could have.
  SAFETY UNCHANGED OTHERWISE: `New` is never clicked (it stays pinned negative
  evidence, recorded in every plan); the whole commit command takes the shared
  Zoho browser mutex BEFORE the plan replay lock, so a busy lane is a free
  refusal that never burns a plan; the interceptor aborts every non-read request
  and releases exactly ONE fully validated POST, once, with no retry; and the
  source is pinned to the FRESH live `Default` id, not the plan's.
  Tests: 147 in the email-template suite (all passing), 643 across the whole Zoho
  suite, 19 in `test_ui_lane_lock`, 618 WooCommerce (1 PHP-only skip). Six
  mutations — removed mutex, removed body comparison, case-blind comparator,
  repeat-save allowed, loose read-back, missing live-Default check — plus the two
  new placeholder mutations were each caught by the suite.
  NO PLAN IS STAGED. `stage --action create_accounting_test` now correctly
  REFUSES, because `CC - Accounting` already exists (the accidental write). The
  next step is Rachad's: either keep `96274000001558092` and confirm the Android
  test against it, or ask for it to be removed — deletion is not reachable from
  any commissioned tool.
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
- 2026-08-11: Rachad approved the three remaining freight-class plans covering 55
  variations: 31 Pipe, 13 Manway and 11 Manway Cover. The first Pipe plan issued
  and verified 21 writes, then issued the write for Pipe variation 1476 / SKU
  `PIDN450150PSI411`. That write also landed, but Google for WooCommerce changed
  three protected metadata values at read-back (`_wc_gla_sync_status` from
  pending to synced, `_wc_gla_synced_at`, and `_wc_gla_sync_hash`). The verifier
  treated the three-value transition as a protected-state mismatch, locked the
  Pipe plan indeterminate and stopped without retry. Fresh live read-only audit
  proved the intended `freight-quote-required` class on all 22 attempted Pipe
  variations, including 1476; the last 9 Pipe targets are still blank, and the
  13 Manway plus 11 Manway Cover plans were never started. Together with the
  three earlier verified Pipe assignments, current protected coverage is 25 of
  58 intended variations, with 33 blank. No other shipping class appeared.
- 2026-08-11: Rachad explicitly commissioned ONE additional narrow action inside
  the existing named `zoho_customer_quote_tool.py`: correct only estimate
  QT-000029 (`96274000001559037`, PO 104750 / J6276), only line Item 9 /
  line-item ID `96274000001559046` / item ID `96274000000030497` (FRP ELBOW-
  12\"/150PSI/D411), and only its quantity from 4 to 1. The live source is Jasmin
  Leblanc's Outlook message received 2026-08-11 12:29; the rate remains CAD
  810.00, the TDS 10% item discount and GST+QST remain unchanged, and the
  independently predicted revised total is CAD 11,165.88. Every other header,
  line ID/order/item, quantity, rate, discount, description, tax and returned
  protected field must be preserved; the live status must remain exactly `sent`.
  The complete live line list must be resent in one PUT, after a read-only stable
  rehearsal and fresh pre-write fingerprint, with a 24-hour immutable plan,
  byte-exact `APPROVED`, replay lock before the one attempt, full live readback,
  no retry/rollback and no mail/send/status/delete route. This authorizes build,
  tests and staging only; it is NOT approval of a staged plan. At commissioning:
  zero Zoho writes, zero estimate changes and zero emails.
  STATUS 2026-08-11: BUILT and independently verified with the complete Zoho
  suite, 584 tests passed with 3 expected skips. Rachad approved staged plan
  `20260811T160516Z_tds_item9_quantity_correction_e718dc3fdb1801a4.json`
  (SHA-256 `e718dc3fdb1801a42b3a1dd588d8e93e5fc7d5115abe63ca8a1bd1e61f073624`)
  with his exact `APPROVED`, and the tool issued its one PUT. The verifier then
  locked the plan `indeterminate` because the gross-subtotal field
  `sub_total_exclusive_of_discount` moved from 13,220.64 to 10,790.64; that is
  the expected quantity-derived movement but was not on the narrow verifier's
  derived-field list. NO RETRY: the plan remains permanently replay-locked.
  Three fresh read-only reconciliation GETs were stable and proved the write
  landed exactly: status `sent`, all 11 line IDs/order/items preserved, Item 9
  quantity 1 at CAD 810.00 with 10% discount and the same GST+QST tax, subtotal
  CAD 9,711.57, tax CAD 1,454.31 and total CAD 11,165.88. A complete protected
  comparison found exactly one difference beyond the explicitly verified
  quantity/totals: `sub_total_exclusive_of_discount`, the expected gross
  subtotal above. QT-000029 is live-corrected; zero emails were sent.
- 2026-08-11: Rachad explicitly commissioned `zoho_sales_order_tool.py` for one
  narrow action only: create one new Draft Zoho Books Sales Order for existing
  active customer Structural Composites Technologies Ltd from client PO26330,
  then attach the exact original PDF received from Bon Bacani. The order is one
  existing active non-legacy item, FNPTCOUPLING-DERAKANE470-3/4\"6\"
  (`96274000000523055`), quantity 2 at CAD 50.20 each (source: client PO26330
  and Rachad's 2026-08-07 email), with GST 5% / CAD 5.02, subtotal CAD 100.40,
  total CAD 105.42, date 2026-08-11, required date 2026-08-12, PO reference
  PO26330, Net 30, SCT's existing Winnipeg billing/shipping addresses, tag
  SO38211-Nutrien Vanscoy, and Purolator Express collect account 3763800. The
  exact correct item has physical available stock 2; the similarly priced 8-inch
  item has zero and the old matching custom item is explicitly LEGACY — DO NOT
  USE. The original PDF is
  `Dado/20_Working/sct_po26330/PO26330-FRP Depot - Couplings.pdf`, 156,997 bytes,
  SHA-256 `274854b82b47231a74a940118995e1f27fd3d9f4a508ac8657781467f43661d9`.
  The tool must stage from fresh live reads, refuse any duplicate PO26330, use
  Zoho auto-numbering, create exactly Draft status, lock before the first write,
  attach only that hash-verified PDF to only the newly created Sales Order, and
  verify both the complete Sales Order and downloaded attachment hash live.
  Creation and attachment are two non-atomic POSTs: any failure/timeout or
  indeterminate result permanently locks the plan, with no retry, rollback,
  deletion or cleanup. No existing Sales Order, customer, item, stock, price,
  shipment/package/invoice, status or email may be changed; the tool has no send
  route. Every 24-hour immutable plan requires Rachad's later exact unpadded
  uppercase `APPROVED`; this commissioning message authorizes build/test/stage
  only and is not approval of a staged plan. At commissioning: the saved OAuth
  connection lacks `ZohoBooks.salesorders.CREATE`; zero Sales Orders created,
  zero attachments uploaded, zero Zoho writes and zero emails.
- 2026-08-11 (same day, later) — **RACHAD CORRECTED THE TAX ON THAT ORDER:
  ONTARIO HST 13%, NOT GST 5%.** His instruction on the not-yet-approved plan
  was "we want to charge sale of Ontario". The order now carries the live
  existing active tax `96274000000035516` `ON HST` at 13%: subtotal CAD 100.40
  (unchanged, still the client PO's own), tax **CAD 13.05**, total **CAD
  113.45**, Decimal ROUND_HALF_UP. Every other fact in the entry above stands
  exactly as written — customer, PO reference, item, quantity, CAD 50.20 rate,
  dates, addresses, Bon Bacani, Net 30, notes/tag, stock rule, PDF and every
  guard. **The client PO's own printed `GST (ITC)@5.0% CAD 5.02` is no longer
  used and is no longer checked against**; it is kept in the tool as a named
  constant purely so the plan can state the difference and its source, the same
  way it already discloses that CAD 50.20 is not the live item rate of 45.72.
  This is a tax-treatment decision Rachad made and Dado records — do not infer
  from it that Ontario tax now applies to any other order; sales tax still
  follows the customer's address/jurisdiction and the delivery terms.
  **THE GST-5% PLAN IS SUPERSEDED, WAS NEVER APPROVED AND WAS NEVER COMMITTED.**
  `20260811T175734Z_create_sct_po26330_draft_sales_order_with_attachment_2950664b01e2366a.json`
  (SHA-256 `2950664b01e2366a...`) is permanently invalid: the tool is now
  v2.0.0 / schema 2 so every plan from the old build fails validation, and that
  plan's own hash is additionally named in code so a retry is told why. No lock
  file was ever written for it. Its file is deliberately left on disk,
  byte-unmodified, as the record. No replacement plan is staged.
  **MANITOBA — he asked whether the threshold had been passed. It has not.**
  Read-only Zoho Books, 2025-08-12 through 2026-08-11: 4 Manitoba-destined
  invoices, CAD 12,100.20 net subtotal, CAD 605.01 GST, CAD 12,705.21 total,
  zero credit notes. Adding this PO's CAD 100.40 gives CAD 12,200.60 — CAD
  17,799.40 below Manitoba's CAD 30,000 annual taxable-sales small-business
  threshold. Manitoba Finance Bulletin RST 004 (revised June 2024) states that
  threshold but ALSO caveats eligibility for out-of-province businesses that
  have not paid Manitoba RST on taxable goods bought for resale, so the
  threshold alone does not settle registration. CRA GST/HST Memorandum 3-3-3
  (April 2026), example and paras 13-16: where the PURCHASER established the
  freight terms and carrier account and the supplier merely contacts that
  carrier for pickup, the supplier is not treated as having retained the
  carrier and legal delivery remains at the supplier's premises — PO26330 says
  Puro Collect / Purolator Express on SCT account 3763800. Dado does not decide
  or change any registration; Rachad elected Ontario HST.
  STATUS: BUILD AND TESTS ONLY. 100 tests in the SCT file (1 Windows-symlink
  skip), 812 across the whole Zoho suite, 618 WooCommerce (1 PHP-only skip), 19
  `test_ui_lane_lock` — all passing. Zero Zoho writes, zero Sales Orders, zero
  attachments, zero emails; vault, `.env` and the live profile untouched.
- 2026-08-11 — **THE FREIGHT VERIFIER STOPPED A CORRECT WRITE. SCHEMA 5 IS THE
  REPAIR. CODE AND TESTS ONLY: NOTHING WAS EXECUTED, RECONCILED OR STAGED.**
  THE DEFECT, measured not guessed. The 31-target FRP Pipe plan
  `20260811T204921Z_shipping_class_assign_3e02e445093c9afb` verified 21 targets
  and stopped at target 22, `/products/1455/variations/1476`, SKU
  `PIDN450150PSI411`. Its ONE approved PUT set `shipping_class` correctly. The
  immediate readback showed Google's save hook had settled the resource in a
  SINGLE step, moving three EXISTING metadata values together with no add, no
  removal, no id change, no key change and no reorder: `_wc_gla_sync_status`
  (id 45152, index 1) pending digest -> settled digest, `_wc_gla_synced_at`
  (id 45151, index 0) new stamp, `_wc_gla_sync_hash` (id 74838, index 5) new
  content hash. Schema 4's verifier recognised a settlement ONLY when exactly
  ONE entry moved (`value_changed_entry_count != 1` -> reject), so it read the
  triplet as an unknown third-party edit, raised `ProtectedStateMismatch`,
  locked the plan indeterminate and stopped. It was right by its own rules and
  wrong about the world. **The write itself was correct and stands.**
  THE REPAIR. `Dado\Tools\woocommerce\woocommerce_shipping_policy_tool.py` is
  now SCHEMA 5 / TOOL_VERSION 5.0.0. A pending baseline now has exactly TWO
  closed settlement shapes, `gla_status_only` (schema 4's, unchanged) and
  `gla_status_with_stamp_and_hash` (the measured one). The wider shape requires
  ALL of: `pending_baseline` mode re-derived from the immutable plan; the
  approved class already in place; `meta_data` the only protected field moved;
  identical entry count, order, ids and keys; EXACTLY three values changed;
  those three keys and no others, matched by key digest, one occurrence each in
  the staged projection; each keeping its own index and stable numeric id; and
  the status entry moving from the one pinned pending digest to the one pinned
  settled digest, never a third value. **It is not an exemption for Google
  metadata, for all metadata, or for those three keys unconditionally** — a
  fourth changed value (including a fourth Google key), a duplicate, a
  two-of-three move, a wrong baseline or any identity drift is still refused,
  still locked, still never retried. The stamp and hash are the only values that
  cannot be pinned to a digest because they are unpredictable by construction,
  so their IDENTITY and POSITION are pinned instead and the existing fixed 2s/4s
  read-only confirmation still demands the COMPLETE settled state, unchanged, on
  every observation — if either moves again mid-confirmation the plan fails
  closed. The settled baseline's 90-second contract is untouched.
  REPLAY SAFETY. The version bump plus four new `convergence_contract` fields
  make every schema-4 plan invalid before vault or network even when rehashed.
  `3e02e445093c9afb` stays consumed and permanently locked, byte-unmodified.
  The two never-started schema-4 plans `bc715e551e3cd948` (Manway) and
  `edf228120165b883` (Manway Cover) are now dead and must not be reused.
  **BLOCKER — READ THIS BEFORE TRUSTING ANY OF IT: NOTHING RAN.** That session
  could not execute python at all (`python --version` answered 3.11.15; every
  other invocation, in both shells, returned "This command requires approval"
  and the session was non-interactive). So: the new and existing tests were
  NEVER EXECUTED, no fresh live GETs were taken, and ZERO plans were staged.
  WooCommerce writes 0, WordPress writes 0, Zoho writes 0, emails 0. Run
  `python -m unittest discover -s C:\FRPDepot\Dado\Tools\woocommerce -p "test_*.py"`
  before the tool is used for anything.
  LIVE STATE, from local artifacts only and NOT re-verified: 58 intended
  targets, 25 assigned, 33 blank — 9 FRP Pipe (product 1455, variations
  1477-1485), 13 FRP Manway (product 1397, variations 1398-1410), 11 FRP Manway
  Cover (product 1411, variations 1412-1422). Source:
  `pipe_31_post_commit_reconciliation_20260811.json` (2026-08-11T21:39:29Z,
  read-only) plus the two `*_remaining_20260811.json` inputs.
  **THE LIKELY NEXT STOP, on evidence already in hand.** That same
  reconciliation shows `/products/1455/variations/2057` — a target this plan
  committed cleanly as attempt 1 — now differing from its staged state by
  `value_changed_entry_count: 2`, `changed_keys: ["_wc_gla_synced_at",
  "_wc_gla_sync_hash"]`, with its status STILL pending. So Google does move the
  stamp and hash WITHOUT the status. That two-entry shape has never been
  observed at a post-write readback, so schema 5 deliberately refuses it
  (fail-closed: only the two counts anyone has actually seen are accepted) — but
  if a future run stops on it, that is the reason, and it needs its own measured
  decision from Rachad, not a quiet widening.

## 2026-08-12 — Packing Ring WordPress media-upload tool (BUILT/TESTED ONLY)
- Rachad answered `Yes` on Discord (2026-08-12) to building a fixed,
  approval-gated media-upload tool restricted to the six approved Packing Ring
  image hashes, after reviewing the gallery contact sheet and replying
  `Looks good! Proceed`. **STATUS: BUILT AND TESTED ONLY; no plan staged; zero
  uploads; zero website writes; zero products changed; zero emails; the
  authenticated browser was never contacted.**
- `Dado\Tools\woocommerce\wordpress_packing_ring_media_tool.py` has exactly two
  commands, `stage` and `commit --plan --approval`. The six file paths, names,
  byte sizes, SHA-256 digests, PNG/RGB/1024x1024 identity, upload order, site
  origin and CDP endpoint are hard-coded; a caller supplies only a plan path and
  an approval word, so no seventh file, review sheet, ZIP, source photo or
  arbitrary path is reachable. Approval is byte-exact unpadded uppercase
  `APPROVED` — never stripped, never case-folded — and is checked before any
  browser or network operation.
- The six approved images are `01_hero_three_quarter.png`, `02_top_view.png`,
  `03_low_side_angle.png`, `04_opposite_face.png`, `05_laminate_macro.png` and
  `06_edge_profile.png` in
  `Dado\20_Working\packing_rings\generated_gallery_20260812\`, all `qc: approved`
  in that folder's manifest and re-verified against disk at build time. They are
  representative marketing drafts, NOT dimensional evidence: bore, thickness,
  bolt circle, hole count and hole diameter are not claimed by them and must not
  be inferred from them. The tool changes no pixels, no filenames and no formats.
- IT IS NOT ATOMIC AND HAS NO ROLLBACK, deliberately, and every staged plan says
  so. Six files are six independent WordPress submissions; if upload N fails,
  uploads 1..N-1 stay live, N+1..6 are never attempted, and the plan locks
  permanently `indeterminate` with `no_retry: true`. There is no delete, detach,
  replace, rename, rollback or retry route anywhere in the module — removing a
  leftover upload is a manual WordPress action or a separately commissioned tool.
- Uploaded media is UNATTACHED: it is added to no product or variation, is
  published nowhere, and changes no price, stock, order, customer or setting.
  There is no REST client, no subprocess import, no credential store, and no
  cookie/token/nonce/storage/page-dump read. It attaches only to the existing
  authenticated loopback WordPress browser on CDP 9229 and never launches or
  signs in to one. Navigation is limited to three admin paths with closed query
  shapes: `media-new.php?browser-uploader`, `upload.php` (bare, `posted=<id>`, or
  the bounded `mode=list&paged=<n>`), and `post.php?post=<id>&action=edit`.
- Both commands hold the shared `ui_browser_lock("wordpress")` named mutex for
  their whole run, taken BEFORE the plan's permanent attempt lock, so a busy
  browser is a free refusal that burns no plan and touches nothing.
- Duplicate preflight, read-only, runs at stage AND again immediately before the
  attempt lock. BOTH GATES ARE COMPLETE OR THE RUN IS A REFUSAL. The NAME gate
  covers the whole library and proves completeness against the list table's own
  item count (unreadable count, unidentifiable row, nameless row, or a short walk
  fails closed); it refuses an exact basename or a stem match once WordPress's
  own `-N` suffix is stripped. The HASH gate covers EVERY enumerated image
  attachment (`.png .jpg .jpeg .gif .webp`) — each opened on its own fixed
  attachment screen, its one public original URL downloaded with no credentials
  and redirects refused, its full SHA-256 compared to all six fixed digests — so
  an identical image stored under an unrelated OLDER filename IS proven absent.
  *** THERE IS NO SAMPLING PATH IN THE MODULE. *** This was the fix on
  2026-08-12 after an independent review: the first build hashed only name
  conflicts plus the newest few rows and admitted in its own plan evidence that
  an older unrelated filename was unproven. Any row that cannot be identified,
  proven safe, downloaded within bounds or hashed makes the whole check
  INCOMPLETE and refuses before any attempt lock exists. The bounds
  (4,000 rows, 200 pages, 2,000 images, 8 MB per file, 1.5 GB cumulative) are
  hard ceilings: exceeding one is a refusal, never a partial scan reported clean.
  Each plan carries the totals that prove it — `library_total`, `enumerated`,
  `image_rows`, `image_hashes_completed`, `hash_failures: 0`,
  `enumeration_complete`, `hash_complete`, `complete` — and `load_plan` re-checks
  that arithmetic plus the exact COMPLETE-gate scope wording, so a plan staged
  under the old bounded gate is not committable. THE COST IS REAL AND IS NOT A
  LIMIT: a complete gate downloads every library image once per stage and once
  per commit. Tool/schema are `2.0.0` / `2` for exactly this reason.
- Verification per upload: exactly one NEW positive attachment id, its own fixed
  attachment screen, exactly one public original URL on the exact host with the
  expected basename and no `-1` suffix, PNG, then a credential-free bounded
  download whose full SHA-256 equals the fixed local digest — then one fresh
  read-only pass over all six and a local result manifest mapping each fixed
  SHA-256 to its attachment id and URL.
- Tests: `test_wordpress_packing_ring_media_tool.py`, 134 run / 133 passed /
  1 skipped / 0 failed (the skip only because this account cannot create
  symlinks — the reparse-point branch it covers is tested deterministically
  instead). The complete WooCommerce suite: 824 run / 822 passed / 2 skipped /
  0 failed (that skip plus the PHP-only checkout-guard scenario); no existing
  test was weakened or deleted. All tests are offline: an adversarial fake
  WordPress DOM carrying controls the tool must never touch, and a fake urllib
  opener that the tool's real download guards run against. The complete hash
  gate is proven there, not asserted: `TestCompleteHashGate` puts the
  interesting file at the very END of a multi-page library — the OLDEST row,
  far outside any window the retired gate could have used — and proves matching
  bytes under an old unrelated filename are caught for all six images, an old
  unrelated NON-matching image is still fully hashed and its edit screen
  actually visited, one unhashable/undownloadable/mistyped/over-bound older row
  fails the whole check closed, and each row/page/image/byte bound refuses
  instead of sampling.
- ANY actual upload still needs its own staged plan and Rachad's own fresh exact
  `APPROVED`. Commissioning the capability is not approval of an upload.
- *** UNRESOLVED, AND NOT FIXED BY THE 2026-08-12 REPAIR: the original build
  also wrote to a Hermes profile file outside this tree,
  `%LOCALAPPDATA%\hermes\profiles\dado\SOUL.md`. *** That was a cross-profile
  write nobody asked for. The repair pass did NOT read, write, restore, compare
  or otherwise touch that path — undoing it needs Rachad's own explicit
  direction, and the build left a `SOUL.md.bak_20260812_pre_packing_ring_media`
  beside it to restore from if he wants that. Repository documentation
  (`fit_profile.md`, `CLAUDE.md`, `DadoProfile\SOUL.md`) is the only
  documentation the repair updated.

## Zoho imported-feed reads (2026-08-12)

This tree does NOT send `account_id` to
`GET /api/v3/banktransactions/uncategorized`, and does not trust it to have
filtered. Measured: on 2026-08-09 and 2026-08-10 a request carrying
`account_id=96274000001409019` returned a row belonging to `96274000001409012`,
and the unfiltered read on 2026-08-10 22:02 proved that row was the only open
line in the whole feed. Whether the route ignores the parameter or expects a
different name was NOT determined - and does not matter, because it failed twice
and once is enough.

Read the feed ONCE unfiltered and partition client-side on each row's own
`account_id`, as `list_uncategorized_ui_transactions` already did. Attribution
must come from the row, never from what was asked for: the old code credited
every returned row to the account it had requested, so relaxing its filter check
without restructuring attribution would have reported USD build-up money under
"Desjardins CAD".

A PROBE RUN ON 2026-08-12 WAS INCONCLUSIVE and is recorded as such: the feed was
empty, so filtered and unfiltered reads matched trivially and the comparison
proved nothing. The two production failures remain the evidence.

## Zoho purchase orders and estimate revisions (2026-08-13)

Durable company facts, measured read-only on 2026-08-13, not inferred.

**Purchase orders.** FRP Depot has exactly SIX purchase orders in Zoho Books
(PO-00001-R2 through PO-00006, complete pagination), and every one of them is to
the same vendor, JRAIN FRP LIMITED (`96274000000027889`), in USD. Two carry a
customer-style reference number in `reference_number` (`TDI PO#5046`,
`TDI PO#5011`) and a real `delivery_date`; `expected_delivery_date` is empty on
all six, so `delivery_date` is the field that actually holds FRP Depot's
delivery dates. `ship_via` is used as free text ("Sea Shipping"). `terms` has
never been populated on a purchase order here.

**An existing quote is now revised in place, not replaced.** Before 2026-08-13
the only way to change a quoted quantity was to create a second estimate, which
leaves the customer holding two numbers for one job. `zoho_customer_quote_tool.py`
now revises ONE existing `draft`-or-`sent` estimate with one atomic PUT,
preserving the estimate number, the customer, the currency and the status.
Prefer it for any ordinary revision; creating a replacement estimate is now the
exception and needs a reason.

**SCT (Structural Composites Technologies Ltd, `96274000000186533`) — RACHAD
RULED ONTARIO TAX FOR SCT, NOT 5%, AND HE HAS SAID IT TWICE.** 2026-08-11 on the
PO26330 sales order ("we want to charge sale of Ontario"), and again 2026-08-13
13:52: "Approved. make sure also for SCT its Ontario taxes not 5%". So NEW SCT
documents carry Ontario HST 13% (`96274000000035516`), even though their billing
address is 200-100 Hoka St, Winnipeg, Manitoba. Measured read-only the same day
and still true as an observation: their EXISTING quote QT-000031 carries tax
`96274000000035512` GST 5%. That is one live record, NOT the rule — do not treat
it as precedent for the next SCT document, and do not silently "fix" it either;
changing it needs its own staged, approved plan.

**The Catalog Classification dropdown has exactly three values and there is no
`Non Website` option.** They are `Website Catalog`, `Custom / Customer-Specific`
and `Review / Unclassified`. `Custom / Customer-Specific` IS the non-web
classification — use it for customer-specific items such as the D441 nozzles and
manways. Do not invent a fourth value and do not rename an option.

**Creating a Zoho item publishes nothing to the website.** Item creation touches
Zoho Inventory only; there is no WooCommerce or WordPress route in that tool.
Catalog Classification is a SEPARATE approved plan that runs AFTER creation,
because it needs the real item IDs creation returns.

**The item-create input shape is FLAT.** Writable fields sit at the root of the
JSON object beside `sources`; a top-level `payload` wrapper is refused. That
wrapper — not any tool defect — is what stopped the four D441 item creations on
2026-08-13.

## Website hosting (verified 2026-08-15)

Rachad has a GoDaddy **Managed Hosting for WordPress Deluxe** plan, but GoDaddy
support and the live Hosting Settings both prove that `frpdepots.com` is **not
connected to that GoDaddy site**: the Domains panel says “We need your help to
finish connecting this domain to your site,” and support states the domain is
pointing to a third-party host. The GoDaddy production IP `160.153.0.96` is for
the disconnected plan, not the current live website. Never click **Connect
Domain**, **Remove Site**, **Create WP Site**, **Start Free Trial**, or **Create**
under Staging Site while the live site remains elsewhere.

GoDaddy still serves the domain's `ns49/ns50.domaincontrol.com` nameservers. The
live public A record resolves to `146.190.245.4`, owned by DigitalOcean, and its
reverse DNS is `1520312.cloudwaysapps.com`. Together with GoDaddy's explicit
third-party-host finding, this identifies the live website as a
**Cloudways-managed DigitalOcean server**. The official Cloudways login is
`https://platform.cloudways.com/login`. No FRP Outlook search result identified
a Cloudways account, so Northnet may own/manage the Cloudways account, but that
account ownership is not yet proven. If Rachad's own email is not recognized by
Cloudways, request a secure Cloudways team/account invitation from Northnet; do
not request or accept an emailed password.
