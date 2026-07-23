# FRP Depot Outlook triage — 2026-07-23

## Worst news first

1. **Airwallex confirmed that FRP Depot's account closure is final.**
   - Services were already terminated, and account access is scheduled to end **2026-08-15**.
   - Source: Airwallex Support email dated 2026-07-17, subject `Re: FW: Important: Account Closure Notification`, message ID ending `r5h3eAAA=`.
   - Required action: verify all currency balances are zero, withdraw any remainder, download statements/transaction records, replace payment instructions and integrations, and retain the closure correspondence before 2026-08-15.

2. **Stripe reported an unrecognized login on 2026-07-22.**
   - Chrome on Windows signed in from Halifax at 19:03 UTC.
   - Source: Stripe email subject `Unrecognised device signed in to your Stripe account`, message ID ending `xZ3wIAAA=`.
   - Required action: open Stripe independently—not through the email link—and confirm the login. If it was not authorized, revoke sessions, reset credentials, enforce MFA, and review users, payouts, webhooks, and recent changes.

3. **The 2026-07-27 payroll bank account needs verification.**
   - Accounting says Zoho Payroll was updated to the new bank account and requested confirmation that the July 27 payroll will use it.
   - Source: accounting email dated 2026-07-23, subject `Change of Bank account for Payroll effective July 27 2026`, message ID ending `xsJGXAAA=`.
   - Required action: verify the bank configuration, funding, and authorization directly in Zoho Payroll and the bank before payroll runs.

4. **Stripe reported that WooCommerce webhook delivery was failing.**
   - Stripe reported 16 TLS failures and warned it would stop sending events after 2026-06-20.
   - Source: Stripe email dated 2026-06-14, subject `Stripe webhook delivery issues for https://wordpress-1520312-6171652.cloudwaysapps.com`, message ID ending `YLFIAAAA=`.
   - Required action: check the current Stripe webhook status and website TLS certificate, send a test event, and reconcile Stripe payments against WooCommerce orders from 2026-06-11 onward.

5. **Paid online order #2127 / SO-00046 appears blocked by quantity, unit, and visible-item-detail errors.**
   - Logistics reported that fitting sizes were not visible and that one pipe was recorded as one foot instead of a complete pipe; packing was stopped.
   - Source: internal logistics email dated 2026-07-14, subject `Re: SO-00046`, message ID ending `r5h3QAAA=`.
   - Required action: reconcile the customer's order against SO-00046, correct the unit/quantities and item attributes, confirm cut lengths, and release packing only after verification.

6. **One current quote request has no reply after the customer supplied final quantities.**
   - Customer: Brian Baldeschwiler / Nashtec LLC
   - Latest message: 2026-07-22 20:15 UTC
   - Requested, per the customer's email:
     - 12 × 6-inch, 150 PSI, 20-foot DK411 FRP pipe
     - 2 × 6-inch, 150 PSI FRP stub flanges
     - 3 × 2-inch, 150 PSI FRP stub flanges
     - 3 × 2-inch, 150 PSI FRP saddle tees
     - joint materials
   - Source: Outlook subject `RE: New submission from Contact`, message ID ending `xZ3wPAAA=`.
   - Required action: prepare catalog/price-list quote content. Every price still requires Rachad's explicit approval before it can appear in a client draft.

7. **A French-language customer order request from 2026-07-13 has no reply.**
   - Customer: Yannick Bédard / Fibre Mauricie
   - Requested, per the customer's email:
     - Derakane 470 flanges: 2 × 6-inch, 3 × 4-inch, 1 × 10-inch, 3 × 3-inch
     - Derakane 470 pipe: 1 × 3-inch, 1 × 4-inch, 1 × 10-inch
     - cut pipe into 4- or 5-foot lengths to reduce transport cost
   - Source: Outlook subject `commande de bride Derakane 470`, message ID ending `rLgf5AAA=`.
   - Required action: confirm stock/pricing and whether this is an order or quote request.

8. **A customer/shipping thread has freight-forwarder scam indicators and remains unanswered.**
   - Latest message: 2026-06-24.
   - The sender asked FRP Depot to contact a specific outside logistics company and include freight in the customer's total payment.
   - Source: Outlook subject `Re: Inquiry`, message ID ending `e5KuyAAA=`.
   - Required action: independently verify the customer and carrier before contacting or paying anyone. Dado did not contact either party.

9. **A supplier's final invoice and repair credit memo remain unacknowledged in email.**
   - Supplier thread: PO-00001-R2.
   - Latest message: 2026-07-09.
   - Source: Outlook subject `Re: Re: Purchase Order from FRP DEPOTS (Purchase Order #: PO-00001-R2)`, message ID ending `kh1fcAAA=`.
   - Attachment values were not reported because attachment extraction is blocked; no amount is being guessed.
   - Required action: reconcile the final invoice and repair credit memo in Zoho after connection.

10. **A banking alert says the Interac Autodeposit registration was replaced.**
   - Alert date: 2026-07-22.
   - Source: Outlook subject `Interac e-Transfer: Your Autodeposit registration has been replaced`, message ID ending `xZ3wHAAA=`.
   - Required action: confirm this was expected as part of the banking change. Do not use email links to verify it.

11. **Invoice INV-000046 still showed CAD 40.94 outstanding, and the customer was asked to resend payment to the new account.**
   - Latest FRP outbound update: 2026-07-22.
   - Amount source: Zoho reminder emails for INV-000046.
   - Required action: verify receipt in Zoho Books before any further reminder.

## Closed / waiting items

- Ex Trade PO 4500021643: logistics confirmed FedEx took the cargo on 2026-07-10. No immediate reply is needed.
- GreatPacific FRP backing-ring opportunity: FRP Depot quoted an expedited 5–6 week ready-to-ship lead time and CAD 24,580 on 2026-06-22; the customer said it would review and return. Amount and lead time source: `operations@frpdepots.com` message ending `dR9-YAAA=`. This is waiting on the customer.
- Supplier PO-00006: the vendor issued a USD 50,396 invoice on net-90 terms and stated the pipes shipped with other orders. Source: vendor email dated 2026-06-30, message ID ending `gIvTKAAA=`. Zoho AP and shared-shipment documents need reconciliation.
- Sodium-silicate tanks: the vendor's 2026-06-15 proposal still exceeded container height and assumed flat-rack shipping with provisional freight. This requires a technical/commercial decision; it is not a standard catalog quote.

## Mailbox processing evidence

- Source mailbox messages found: **1,178** — Microsoft Graph audit.
- FRP Depot-scope messages classified: **769** — `message_triage.jsonl`.
- Different-company hard-wall messages excluded and local copies purged: **389** — audit purge receipt.
- Other clearly non-FRP-scope messages excluded: **20** — audit purge receipt.
- FRP conversations indexed: **269** — `triage_metrics.json`.
- Open-conversation candidates generated: **140** — `triage_metrics.json`; automated and stale items remain in the index but were not treated as client work.
- Mailbox modified: **No**.
- Emails sent: **None**.
- Drafts created: **None**.
- Outlook send permission: **Absent**.

## Attachment blocker

- In-scope unique attachments queued: **243**.
- Attempt 1 failed on a binary-scanner code defect.
- The defect was fixed and tests passed.
- Attempt 2 processed **200 of 243** attachments, then hit the 600-second execution limit before it could commit the extraction report.
- Per the two-failure rule, extraction was stopped. No attachment values have been guessed.

## Operational knowledge recorded

The FRP Depot fit profile now records mailbox-verified facts:

- Website/domain: `www.frpdepots.com` / `frpdepots.com`.
- Repeated business/contact address: 4507 Ferguson Dr., Brockville, Ontario, Canada K6T 1A9. Warehouse status is not confirmed.
- Catalog-based FRP products discussed include pipe, fittings, stub flanges, saddle tees, grating, profiles, coatings, and unlisted/custom items.
- Sales replies direct customers to the website for listed pipe/fittings and use separate quotes for coatings/unlisted items. The formal price-list source of truth still requires Rachad's confirmation.

## Signature blocker

The mailbox contains several signature variants with different phone lines. No variant was set as the standard signature because Rachad has not confirmed which exact block is current. Until confirmed, Dado will block client drafts rather than invent a signature.
