---
name: zoho-client-po-reference
description: Use for Zoho Sales Orders/Invoices. Enforce client PO.
---

# Zoho Client PO Reference

Use this specialist skill for the Reference# and rendered-document gates on every FRP Depot Zoho Sales Order or Invoice task: creating, revising, auditing, converting, reviewing, or preparing one for Rachad.

For a new/current customer order, load `frp-order-workflow` first and validate its single order packet. This skill owns exact PO propagation and post-write visibility; it does not repeat customer/thread/item/tax/stock intake.

## Standing rule from Rachad — 2026-08-12

Every Sales Order and Invoice must reference the customer's own PO in Zoho's visible **Reference#** field whenever the customer issued one. The same PO must flow from the authoritative customer request or PO document to every related Sales Order and Invoice.

A PO stored only in notes, an attachment, an internal audit field, a quote number, an FRP Depot Sales Order number, or an email body does **not** satisfy this rule.

## Required Reference# handoff

1. Read the validated `frp-order-workflow` packet and its original evidence. Re-read the live customer thread immediately before staging; do not trust a stale packet if a newer external message exists.
2. Inspect the customer's original PO attachment when one exists and confirm its live hash/size still match the packet.
3. Confirm the packet's client PO value byte-equals the direct source. Preserve meaningful prefixes, suffixes, slashes, dashes, and leading zeroes exactly as the customer uses them.
4. Refuse FRP Depot document numbers (`QT-...`, `SO-...`, `INV-...`) as substitutes for a customer PO.
5. Put the client PO in Zoho's `reference_number` / visible **Reference#** field on every related Sales Order and Invoice. If the packet carries Rachad's explicit no-PO exception, keep Reference# blank.
6. If evidence conflicts or the live source changed, stop and ask Rachad one question. Never invent, infer, or silently normalize a PO.
7. Before staging any write, show Rachad the source, exact proposed Reference#, affected document number/ID, current value, replacement value, and any linked-document inconsistency.
8. Use only a commissioned named Zoho stage-then-commit tool. Staging makes no write. Each immutable plan needs Rachad's later exact one-word `APPROVED`; never infer or reuse approval.
9. After a write, perform a fresh live API read and prove `reference_number` equals the approved client PO exactly.
10. Run the read-only `C:\FRPDepot\Dado\Tools\orders\zoho_rendered_order_reference_verifier.py` for the exact record. Use `--expected-reference <exact-client-po>` for an issued PO, or `--expect-no-reference` only for the packet's Rachad-approved no-PO exception. It must prove both the API `reference_number` and the fresh rendered caption/value: Quote **Reference#**, Sales Order **Ref#**, Invoice **P.O.#**. Under the no-PO exception, both API value and rendered caption must be absent. An API field alone is not enough.
11. Verify linked Sales Orders and Invoices independently. A correct Invoice does not prove its Sales Order is correct, or vice versa.
12. Return to the umbrella packet before completion and account for every requested follow-up separately, including forwarding or emailing. Drafts only; never claim a requested forward was done when only the Zoho record was corrected.

## Historical repair discipline

- Start with a complete read-only Zoho audit of all Sales Orders and Invoices.
- Recover missing PO values only from direct customer email/attachment evidence.
- Classify each record as exact, missing, wrong/internal-reference, ambiguous, or no defensible PO evidence.
- Do not mass-write ad hoc. Build immutable repair plans only for exact evidence-backed corrections and show each affected record and value to Rachad.
- Preserve all non-reference business fields and linked records. Any record outside a commissioned tool's status/scope is a blocker requiring a separately commissioned narrow tool.

## Zoho live-read pitfall

- Zoho regenerates an invoice's secure `invoice_url` across otherwise identical GETs. Record it as volatile evidence, but do not include it in the stable business-state digest or pre-commit equality check.
- Do not broadly ignore metadata: every other returned business field remains protected unless repeated read-only diagnostics prove another field is non-business and volatile.

## Verification checklist

- Full client thread read.
- Original PO inspected when present.
- Client PO source recorded.
- Sales Order Reference# exact.
- Invoice Reference# exact.
- Rendered Quote **Reference#**, Sales Order **Ref#**, or Invoice **P.O.#** visibly shows the exact client PO; under an approved no-PO exception, the API value and rendered caption are both absent.
- No internal FRP document number substituted.
- All other business fields unchanged unless separately approved.
- Requested forwarding/email handled separately under Drafts Only.
- Receipt recorded with live document ID and evidence artifact.

## Proven incident

On 2026-08-12, the client wrote: “Can you please include the SHM PO on the invoice and forward to elaineiverson@ralmax.com Thank you.” The replacement Draft invoice `INV-000053` correctly stored `0000031` in live Zoho `reference_number`, but completion reporting initially verified the API field without proving the rendered document displayed it and did not foreground the separate forwarding request. This skill prevents both failures.
