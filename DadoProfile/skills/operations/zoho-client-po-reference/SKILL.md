---
name: zoho-client-po-reference
description: Use for Zoho Sales Orders/Invoices. Enforce client PO.
---

# Zoho Client PO Reference

Use this skill for every FRP Depot Zoho Sales Order or Invoice task: creating, revising, auditing, converting, reviewing, or preparing one for Rachad.

## Standing rule from Rachad — 2026-08-12

Every Sales Order and Invoice must reference the customer's own PO in Zoho's visible **Reference#** field whenever the customer issued one. The same PO must flow from the authoritative customer request or PO document to every related Sales Order and Invoice.

A PO stored only in notes, an attachment, an internal audit field, a quote number, an FRP Depot Sales Order number, or an email body does **not** satisfy this rule.

## Required workflow

1. Read the complete live customer email thread before preparing the document. Do not reduce a multi-part request to only one requested action.
2. Inspect the customer's original PO attachment when one exists.
3. Identify the exact customer-issued PO number from direct evidence. Preserve meaningful prefixes, suffixes, slashes, dashes, and leading zeroes exactly as the customer uses them.
4. Refuse FRP Depot document numbers (`QT-...`, `SO-...`, `INV-...`) as substitutes for a customer PO.
5. Put the client PO in Zoho's `reference_number` / visible **Reference#** field on every related Sales Order and Invoice.
6. If no customer PO was issued or the evidence conflicts, stop and ask Rachad one question. Never invent, infer, or silently normalize a PO.
7. Before staging any write, show Rachad the source, exact proposed Reference#, affected document number/ID, current value, and replacement value.
8. Use only a commissioned named Zoho stage-then-commit tool. Staging makes no write. Each immutable plan needs Rachad's later exact one-word `APPROVED`; never infer or reuse approval.
9. After a write, perform a fresh live API read and prove `reference_number` equals the approved client PO exactly.
10. Also verify the rendered Zoho document/PDF/preview visibly shows **Reference#** and the PO. An API field alone is not enough when the document will be shown to a client.
11. Verify linked Sales Orders and Invoices independently. A correct Invoice does not prove its Sales Order is correct, or vice versa.
12. Read the complete customer request again before completion. Report every requested follow-up separately, including forwarding or emailing. Drafts only; never claim a requested forward was done when only the Zoho record was corrected.

## Historical repair discipline

- Start with a complete read-only Zoho audit of all Sales Orders and Invoices.
- Recover missing PO values only from direct customer email/attachment evidence.
- Classify each record as exact, missing, wrong/internal-reference, ambiguous, or no defensible PO evidence.
- Do not mass-write ad hoc. Build immutable repair plans only for exact evidence-backed corrections and show each affected record and value to Rachad.
- Preserve all non-reference business fields and linked records. Any record outside a commissioned tool's status/scope is a blocker requiring a separately commissioned narrow tool.

## Verification checklist

- Full client thread read.
- Original PO inspected when present.
- Client PO source recorded.
- Sales Order Reference# exact.
- Invoice Reference# exact.
- Rendered document visibly shows Reference#.
- No internal FRP document number substituted.
- All other business fields unchanged unless separately approved.
- Requested forwarding/email handled separately under Drafts Only.
- Receipt recorded with live document ID and evidence artifact.

## Proven incident

On 2026-08-12, the client wrote: “Can you please include the SHM PO on the invoice and forward to elaineiverson@ralmax.com Thank you.” The replacement Draft invoice `INV-000053` correctly stored `0000031` in live Zoho `reference_number`, but completion reporting initially verified the API field without proving the rendered document displayed it and did not foreground the separate forwarding request. This skill prevents both failures.
