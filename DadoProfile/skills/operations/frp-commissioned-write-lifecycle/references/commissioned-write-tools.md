# Commissioned write tools - the tool-by-tool list (reference for SOUL Hard Rule 3)

Moved out of `DadoProfile\SOUL.md` Hard Rule 3 on 2026-08-21 (the SOUL had grown
to 73 KB, past the 65,280-char context cap, and the middle - this list - was
being cut out of Dado's own prompt). The SOUL keeps the rule; this file keeps
the list, rewritten to the authority-and-reversibility model Rachad approved on
2026-08-21 ("Go on all of them"). The verbatim pre-trim text, with every
commission date, fixed ID, hash and STATUS line as it stood, is
`C:\FRPDepot\DadoProfile\SOUL.md.bak_20260821_pre_autonomy_trim` (lines
103-575), and the full Zoho commission records are
`Dado\Tools\zoho\COMMISSIONS.md` - read that file, not this index, before
touching a Zoho write tool.

## The model (SOUL Hard Rule 3, short)

- HIS WORD IS THE AUTHORITY. A clear instruction from Rachad, in his own message
  on Telegram, Discord or the dashboard chat, authorises the WHOLE job - every
  plan that job needs. "Yes", "go", "do it", "go ahead", a plain instruction
  all count. Detection is the one shared `is_clear_affirmative` (Aze's
  `C:\AgentTeam\Aze\Tools\orchestration\budget_owner_attestation.py`, carried
  verbatim in `Dado\Tools\common\owner_authority.py` - never a second variant).
- REVERSIBLE tools: stage shows the exact change; apply commits it on his
  instruction. The tool captures the LIVE state it is about to change and
  keeps it beside the plan. The way back is a restore route where the tool
  carries one; otherwise the captured pre-write state and Drive/Zoho history,
  stated in the plan before he says go. One receipt per write naming what,
  where, when and the backup path.
- MONEY / IRREVERSIBLE tools: stage, then Rachad's own unambiguous go to THAT
  plan, sent AFTER the plan was written (the plan-before-approval timestamp
  check stays). "APPROVED" is no longer the required word; "yes go ahead" to the
  shown plan counts. It must be his message on an authenticated lane, never
  relayed or quoted text.
- NO PERMANENT LOCKS. A failed commit leaves the plan "needs re-stage": re-read
  live, re-stage (diff shown), apply again; his original instruction covers the
  retry unless the scope changed. Money tools: a failed commit is reported and
  re-staged - no silent retry, no permanent lock.
- Batches: one instruction covers the batch; per-line writes continue when one
  line fails; the failed lines are recorded and re-staged alone.
- UNCHANGED WALLS: drafts only (no send path anywhere); no keys or tokens; no
  deletes in Zoho (the only deletions anywhere are the two-step, IRREVERSIBLE
  WordPress orphan-media and fixed-origin cleanups below); company walls as
  written in the SOUL; no new walls he did not ask for.
- RESTORE column below is what the code carries on 2026-08-21, not what the
  programme intends: a route is named only where the tool actually has it
  (`grep add_parser` in the tool). Restore routes exist in exactly six tools -
  `zoho_inventory_item_tool` (`restore-name-sku`, `restore-group-option-rename`),
  `zoho_inventory_category_tool` (`restore-assign`),
  `zoho_inventory_classification_tool` (`restore-assign`),
  `zoho_inventory_price_tool` (`restore`), `google_catalogue_publish_tool`
  (`restore`) and `woocommerce_change_tool` (`restore`) - plus `rollback` in
  the freight-quote journey pair. Every other reversible row says "no restore
  route yet", names what the code captures before the write (or that it
  captures nothing), and the way back is to re-stage from that captured state
  or from Drive / Zoho version history - say so in the plan before he says go.
  A tool with no way back at all says that in the plan too.
- NOT YET ON THE SHARED MODULE: the WordPress UI tools, the tax-delivery and
  FNPT fixed tools and the freight-quote journey pair do not import
  `Dado\Tools\common\owner_authority.py` yet; as built they still take the
  exact word APPROVED and lock a failed plan permanently. The model above is
  the rule; for those tools "re-stage" means a fresh plan, and the permanent
  lock is a code gap to report, not a rule to follow.

## Zoho (`Dado\Tools\zoho\`)

| Tool | What it may change | Class | Restore |
|---|---|---|---|
| `zoho_tool.py` | Nothing - connector and GET-only reports | read-only | n/a |
| `zoho_inventory_item_tool.py` (2026-07-23; option rename 2026-08-08) | Create an item; rename / re-SKU an existing item; the one fixed FRP FW PIPE SIZE option rename `30` -> `30"` | REVERSIBLE | `restore-name-sku --plan` / `restore-group-option-rename --plan`: the name/SKU or option label captured in `<plan>.live-before.json` before the PUT is put back (one PUT). A CREATED item has no restore route and no set-inactive route (Zoho deletes are walled) - it stays; say so in the plan before he says go |
| `zoho_inventory_classification_tool.py` (2026-08-07) | Create the one fixed Catalog Classification dropdown; assign only its three fixed values (`Website Catalog` / `Custom / Customer-Specific` / `Review / Unclassified`) to existing items | REVERSIBLE | `restore-assign --plan`: the classification captured in `<plan>.live-before.json` before the PUTs is re-assigned per item. The dropdown create has no restore route and the field cannot be removed (Zoho UI) - say so |
| `zoho_inventory_category_tool.py` (2026-08-07) | Item category assignment (fixed category set); category create | REVERSIBLE | `restore-assign --plan`: the categories captured in `<plan>.live-before.json` before the PUTs are put back. A created category has no restore route (no delete in Zoho) - say so |
| `zoho_inventory_price_tool.py` (2026-08-10) | ONLY the sales `rate` on EXISTING items whose SKU begins `FNPTCOUPLING-DERAKANE411-` / `-470-`, to supplier USD unit cost x 3.6, half-up, CAD. No purchase rate, stock, name, SKU, creation or deletion | REVERSIBLE | `restore --plan`: the live rates captured in `<plan>.live-before.json` before the PUTs are put back per line. Batches are per-line PUTs; a failed line no longer locks the plan - it is recorded and re-staged alone |
| `zoho_customer_quote_tool.py` (2026-07-23; corrections 2026-08-10/11; general revision 2026-08-13) | Create a customer; create a DRAFT estimate; the two fixed TDS corrections (QT-000029 discount `10` -> `"10%"` and Item 9 quantity 4 -> 1 - both landed, replay-closed); the GENERAL in-place estimate revision (`stage-estimate-revision` / `commit-estimate-revision`): one estimate in exactly `draft` or `sent`, header reference/date/expiry/notes/terms and per-existing-line quantity/rate/discount/description/tax; customer, number, currency, lines set and every lifecycle/mail field preserved; a percentage discount is the string `"10%"`, never a bare number | REVERSIBLE (draft estimates and their corrections) | no restore route yet (2026-08-21). Pre-write capture: `commit-estimate-revision` writes the live estimate header and lines to `<plan>.live-before.json` before its one PUT (the two fixed TDS corrections are landed and replay-closed). Way back: re-stage `stage-estimate-revision` from that captured `.live-before.json` and apply on his go. A created customer and a created draft estimate STAY - no delete route and no set-inactive route - and the plan says so before he says go |
| `zoho_email_template_tool.py` (2026-08-10) | Create one of exactly FOUR organisation-wide invoice email templates, clones of `Default` with one name and one fixed CC list (`CC - Accounting`, `CC - Logistics`, `CC - Operations`, `CC - All`, all @frpdepots.com). Two-phase: `CC - Accounting` first so Rachad can prove it on Android; the other three after his own test confirmation. No update/rename/set-default/send route; the module has no mail transport. The ONE delete route (`stage-delete` / `commit-delete`) is hard-wired to template 96274000001558092 - the 2026-08-11 accident Rachad asked to have removed through a commissioned path - refuses every other row, and that template vanished on 2026-08-11 (status ledger), so it has nothing left to reach | REVERSIBLE by Rachad's list, BUT Zoho publishes no create API and a created template has no way back | no restore route (2026-08-21); pre-write capture: none (a create has no prior state). A created template stays - the fixed delete route above cannot reach it. Say so before creating |
| `zoho_historical_client_po_reference_tool.py` (2026-08-12) | Reference-only repair of the client PO number on twelve fixed historical sales orders / invoices (the field Zoho prints as `Ref#`) | MONEY - a field on landed sales orders / invoices, which are financial records: stage, then his go to THAT plan after it was written | no restore route (2026-08-21). Pre-write capture: each fixed record's `before` reference is pinned in the tool and re-read live at stage (`current_reference` must still equal it), so the plan carries it. Way back: a new plan that writes the captured reference back, with his go after that plan |
| `zoho_woo_sku_pair_tool.py` (2026-08-06) | Paired Zoho + WooCommerce SKU correction, one combined plan, read-back in both systems | REVERSIBLE | no route of its own - each writer's own route: `zoho_inventory_item_tool.py restore-name-sku --plan <zoho child plan>` and `woocommerce_change_tool.py restore --plan <woo child plan>`, each from its own `.live-before.json`. A failed child leaves the pair "needs re-stage" with the landed half recorded |
| `zoho_invoice_revision_tool.py` (2026-08-10) | (a) revise ONE existing Books invoice (draft or sent, no payment/credit/package/shipment/recurring): customer_id to an EXISTING customer, reference_number, date, due_date, owned address ids, notes, terms, per-existing-line quantity/rate/discount/description/tax_id; every line resent once in order, none added or dropped. (b) create ONE NEW draft invoice, Zoho numbering, existing active customer and items only, explicit source per value, Decimal totals shown and verified. Cannot email, delete, void, mark-sent | MONEY | Two-step. A failed commit is reported and re-staged (no permanent lock). No restore route: the plan's own `before_state` holds the pre-write invoice, and a revert of a landed revision is a new staged revision built from it, with his go after that plan |
| `zoho_banking_reconciliation_tool.py` (2026-08-07) | Match / categorize / unmatch / uncategorize imported bank lines; correct source/destination account on an existing transfer | MONEY | Two-step. Unmatch/uncategorize are the Zoho-native reversal of match/categorize and need their own staged plan and his go |
| `zoho_sales_order_tool.py` (2026-08-11) | ONE fixed draft Sales Order for Structural Composites Technologies against PO26330 (ON HST 13%, Rachad's correction of 2026-08-11), then attach the PO PDF; two non-atomic POSTs | MONEY | Two-step; every constant is in the tool; a created draft stays - the plan says so |
| `zoho_purchase_order_tool.py` (2026-08-13; amended 2026-08-21) | Create ONE NEW draft Purchase Order (`POST /books/v3/purchaseorders` only; vendor must already exist as a vendor, items must exist; Zoho numbering; duplicate walk before the write). 2026-08-21: `ZohoBooks.purchaseorders.UPDATE` removed from its refusal list because the J26-403 tool holds it on the shared connection; this tool still has no PUT/PATCH/DELETE | MONEY | Two-step; a created draft remains (no delete/void route) and every plan says so; scope CREATE prepared, not live until reauthorization |
| `zoho_j26_403_revision_tool.py` (2026-08-21) | Two independent fixed PUTs, each its own plan and go: PO-00010 (emailed, open) and QT-000034 (accepted) each gain the six dip tubes and twenty-four lifting lugs as free-text lines; no item is ever created; the lug supplier cost is never guessed - it needs a cited source artifact with matching SHA-256 | MONEY | Two-step. As delivered it CANNOT COMMIT: five Zoho contract facts are unproven and commit refuses before any network call; Rachad's "Proceed" authorised the build, not a write |
| `zoho_backing_ring_stock_tool.py`, `zoho_backing_ring_eight_stock_tool.py`, `zoho_backing_ring_eight_valuation_correction_tool.py` (2026-08-11/12) | Fixed one-shot inventory adjustments (4"/10" merge + rates; 713 pcs on eight items; CAD 78,816.51 value correction) | CLOSED / SPENT - all three landed and verified, replay-closed | none - nothing is retried; a new adjustment needs a new commission |

Zoho stays READ-ONLY outside this table: no ad-hoc write API calls, no deletes, no
stock adjustments outside a commissioned tool, no sending anything. Estimate
scope stays `ZohoBooks.estimates.UPDATE` and nothing wider; there is
deliberately no DELETE, ALL or fullaccess scope anywhere.

## WooCommerce / WordPress (`Dado\Tools\woocommerce\`)

| Tool | What it may change | Class | Restore |
|---|---|---|---|
| `woocommerce_audit_tool.py`, `packing_observation_tool.py`, `catalogue_presentation_release_qa_tool.py` | Nothing - read-only audits / collectors | read-only | n/a |
| `woocommerce_change_tool.py` (2026-07-24; image alt 2026-08-08) | Product / variation create (forced draft) or update; existing-product image ALT text only (complete gallery, unchanged ids and order) | REVERSIBLE | `restore --plan`: the product / variation fields captured in `<plan>.live-before.json` before the PUT are put back through the same update route. A CREATE (forced draft) has no restore route - the draft product / variation stays and the plan says so |
| `woocommerce_shipping_policy_tool.py` (2026-08-09) | Create only the fixed `Freight Quote Required` class; assign / remove only that class on enumerated existing products / variations | REVERSIBLE | no restore route yet (2026-08-21); pre-write capture: the plan's per-target baseline snapshot, re-proved immediately before the PUT. Way back: stage the inverse action - `shipping_class_remove` undoes `shipping_class_assign` on the same targets and vice versa - and apply on his go. A created class has no delete route; say so |
| `woocommerce_tax_delivery_tool.py` | The fixed 17 delivery tax-rate PUTs (non-atomic) | REVERSIBLE (rates) - but the tool is FIXED to its one correction and not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: the plan pins the complete pre-write 18-row table and the semantic settings / classes / versions. Way back: the tool writes only its fixed target table, so putting the old rates back needs a new commission - say so in the plan. As built it stops at the first uncertain PUT with a permanent attempt lock (no per-row re-stage yet); the rest needs a fresh plan |
| `woocommerce_fnpt_catalog_cleanup_tool.py`, `woocommerce_fnpt_go_live_tool.py` (2026-08-13) | Fixed FNPT parent 2061 cleanup and `{"status": "publish"}` | REVERSIBLE (content) - not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: cleanup - the plan's `parent_before` full and protected fingerprints (hashes, not the record); go-live - the plan's `prewrite_snapshot` (status draft, variation fingerprints, gallery). Way back: no tool carries the inverse (re-adding the option / category 17, or setting 2061 back to draft - `woocommerce_change_tool` has no product status field); it needs a new plan under a new commission - say so in the plan |
| `wordpress_plugin_deployment_tool.py` (2026-08-09) | Replace, activate or deactivate ONLY `FRP Depot Freight Checkout Guard` through the authenticated WordPress UI (CDP 9229); activation runs an anonymous checkout test and auto-deactivates on failure | REVERSIBLE - not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: the plan's live plugin-row fingerprint (version, status), and an activation whose anonymous checkout test fails deactivates itself. Way back: `stage-deactivate` is the inverse of activate; a replaced copy has NO route back - each replace route is one fixed from -> to version step (1.0.0 -> 1.0.1, 2.0.6 -> 2.0.7, 2.0.7 -> 2.0.8) and there is no delete; say so in the plan |
| `wordpress_media_guard_deployment_tool.py` | Install (inactive, only when absent), activate, deactivate exactly the pinned Media Mutation Guard plugin; replace the exact 1.0.5 with 1.0.6 once | REVERSIBLE - not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: the plan's fixed plugin-row state. Way back: `stage --action deactivate` is the inverse of activate; an installed or replaced copy has no route back (no delete; replace is fixed 1.0.5 -> 1.0.6); say so |
| `catalogue_presentation_deployment_tool.py` | Install-or-replace (inactive), activate, deactivate the one catalogue-presentation plugin | REVERSIBLE (catalogue presentation) - not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: the plan's fixed plugin-row state; an activation whose validation contract fails is auto-deactivated once. Way back: `stage-deactivate` is the inverse of activate; install-or-replace has no route back (no delete, no earlier artifact); say so |
| `freight_quote_journey_deployment_tool.py`, `wordpress_freight_quote_journey_tool.py` | The fixed freight-quote companion: `stage`, `apply`, `rollback` | REVERSIBLE - not yet on the shared module | `rollback --plan` (exists): deactivates the exact run's fixed row only; it never deletes files, forms or entries |
| `wordpress_hetron_guide_lifecycle_tool.py`, `wordpress_hetron_private_history_deployment_tool.py` (2026-08-13) | Retire one obsolete Hetron attachment route; replace the fixed plugin and move five byte-pinned history files into a hidden directory, bytes preserved | REVERSIBLE (content; historical bytes preserved) - not yet on the shared module | no restore route yet (2026-08-21); pre-write capture: guide lifecycle - the plan's `before` (full and protected SHA-256 of attachment 1832 plus the five asset hashes); private history - the plan's `before` snapshot per action. Every historical file keeps its bytes. Way back: making 1832 public again or moving the five files back needs a new plan under a new commission - no tool carries it; say so in the plan |
| `wordpress_packing_ring_media_tool.py` (2026-08-12), `wordpress_product_family_media_tool.py` (2026-08-15), `wordpress_open_manway_gallery_recovery_tool.py` (2026-08-21) | Upload the fixed approved images (hash-pinned) and assign hero / gallery on the fixed products (1368, 1397, 1411, 1423, 1455); complete duplicate preflight by name AND hash | REVERSIBLE (media / content) - not yet on the shared module | no restore route yet (2026-08-21). Pre-write capture: the product-family and open-manway plans carry `before_gallery` (hero / gallery before the PUT); the packing-ring tool uploads UNATTACHED media only and captures nothing to restore. Way back: putting the captured hero / gallery back needs a new plan - no tool carries a free gallery write (`woocommerce_change_tool` touches ALT text only); an uploaded file STAYS (no delete route by design). Uploads are NOT atomic: a failure at upload N leaves 1..N-1 live, then re-stage the rest. The plan says all of this before he says go |
| `wordpress_orphan_media_cleanup_tool.py` (2026-08-20), `wordpress_orphan_media_correction_tool.py` (2026-08-21) | Permanently delete / correct the four orphan attachment records 5521, 5523, 5525, 5527 | IRREVERSIBLE (deletion) - with the row below, the only deletions anywhere in the tree | Two-step; no way back - the plan says so |
| `wordpress_fixed_origin_file_cleanup_tool.py`, `wordpress_fixed_origin_cleanup_plugin_recovery_tool.py` (2026-08-21) | Delete the four exact unregistered origin files; remove the inactive one-use cleanup plugin | IRREVERSIBLE (deletion) | Two-step; no way back - the plan says so |

Every WordPress UI tool attaches only to the existing authenticated CDP 9229
browser, holds `ui_browser_lock("wordpress")` for the whole run (taken BEFORE any
attempt lock, so a busy browser is a free refusal), and makes no REST call, reads
no cookie or token, and sends no email.

## Google (`Dado\Tools\google\`)

| Tool | What it may change | Class | Restore |
|---|---|---|---|
| `google_tool.py`, `analytics_tool.py`, `frp_search_console_report.py`, indexer / backfill / reference | Gmail read + DRAFTS ONLY (TDI screen on); Drive read (unrestricted); GA4 / Search Console read | read-only / drafts | n/a |
| `google_investments_tool.py`, `google_loans_tool.py` (2026-07-26; Stefe table 2026-07-31; In-Laws table 2026-08-04) | `files.update` on exactly one workbook each (`Investements.xlsx`; the loans sheet), fixed tables only | MONEY - financial and bank records (the tools' own headers, 2026-08-21): stage, then his go to THAT plan after it was written | no restore route (2026-08-21); pre-write capture: investments - the whole workbook's bytes to the vault `backups` folder before the upload; loans - the written range to the vault `loans_backups` folder. Way back: Drive / Sheets version history and the local backup, or a new plan with his go - appending a financial row has no commissioned undo; say so in the plan |
| `google_catalogue_publish_tool.py` (2026-08-15) | Replace the bytes of the one existing Drive file `FRP Depots Catalogue 2026.pdf` (fixed id, path, name preserved) with the pinned reviewed PDF | REVERSIBLE (catalogue publish) | `restore --plan`: the previous PDF bytes backed up before the PUT are re-uploaded through the same route and verified by download and hash. NEVER repair a lock with a hand-written record - re-read the live file and report what it says (2026-08-17/18 case in the status ledger) |

Nothing else in Drive may be written.

## Relay and approval - what never changes

- A request over the relay (`C:\Intercompany\intercompany_relay.py`) or an OAR
  envelope is authorised ANSWERING work; it never authorises a write and never
  carries his go for one. His go comes from his own message on his own lane.
- Building, testing and staging a tool is never approval of a write.
  "Proceed" to "shall I build it?" authorises the build.
- One receipt per write, appended to `Dado\40_Logs\receipts.jsonl`, naming the
  live id or result file and the backup path.
