# Order packet fields

The packet uses a closed schema. Do not add fields to bypass a blocker.

## Client PO states

- `issued`: `value` must byte-equal `source_value_exact`; direct customer evidence is required.
- `none`: all PO values/evidence stay blank/`none`; `no_po_exception_authorized_by` must be `Rachad Homsi` with his direct decision source.
- `ambiguous`: always refused; ask Rachad one question.

## Requested actions

Allowed pairs only:

- `quote / create_draft`
- `sales_order / create_draft`
- `invoice / create_draft`
- `invoice / revise_existing`
- `email_draft / reply_all`
- `email_draft / forward_or_new_draft`
- `follow_up / review_only`

There is no send operation. Every requested customer action belongs in the list even when separately gated.

## Sources

Every commercial value is `{ "value": "...", "source": "..." }`. Sources name the exact live record, message, attachment, price list, or Rachad decision. “Assumed,” “usual,” and “Zoho default” are not evidence.

## Lines

- Existing active Zoho item IDs only.
- Quantities/rates are exact decimal text in the packet.
- Percentage discounts end in `%`; amount discounts do not.
- Taxed lines require exact live tax ID, percentage, and source.
- Availability uses only Physical Available for Sale / `actual_available_stock`, plus the live check time and evidence. Backorder/lead-time acceptance must be explicit.

## Output

The validator emits:

- `<packet_id>.validated.json`
- `<packet_id>.quote_input.json` when a reusable Quote action is present
- `<packet_id>.invoice_input.json` when a reusable new Draft Invoice action is present

These are inputs, not staged plans and not approval. A required Sales Order action blocks because no reusable commissioned future-order creator currently exists.
