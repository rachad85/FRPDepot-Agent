# STATUS ledger - what is connected, what landed, what is not evidenced

Moved out of `DadoProfile\SOUL.md` (section "## STATUS", pre-trim lines 882-1000,
plus the STATUS lines that sat inside Hard Rule 3, lines 213-223, 302-323,
342-357, 378-392, 417-428, 471-474, 538-542 and 559-575) on 2026-08-21 so the
SOUL fits under 25 KB. The SOUL keeps one line per system; this file keeps the
dated record. The verbatim pre-trim wording is
`DadoProfile\SOUL.md.bak_20260821_pre_autonomy_trim`. Keep this file current:
what is BUILT is a measured fact and lives here; what is PERMITTED is Rachad's
decision and lives in the SOUL.

## Outlook
- CONNECTED (read + draft, verified 2026-07-22). Mailbox info@frpdepots.com;
  scopes User.Read, Mail.ReadWrite, Calendars.Read - NEVER Mail.Send.

## Zoho Books / Inventory
- CONNECTED and live-verified 2026-07-25. Reads are available; writes only
  through the named tools (reference `commissioned-write-tools.md`). Never
  simulate or invent results.
- 2026-08-10: `zoho_invoice_revision_tool.py` (revise + create draft) BUILT AND
  TESTED ONLY; OAuth reauthorization pending at that date; ZERO Zoho writes,
  ZERO emails.
- 2026-08-10 (email templates): BUILT, TESTED, ONE read-only
  `create_accounting_test` plan STAGED. Its commit cannot run: Zoho publishes
  no documented create API, its settings XHRs carry an x-zcsrf-token you may
  not read or copy, and capturing the native Save contract needs the fixed
  `New` form opened, which the commission prohibited. The tool refuses before
  its lock. ZERO templates created. (The accidentally created `CC - Accounting`
  96274000001558092 vanished 2026-08-11 with no commit lock and no receipt -
  see C:\FRPDepot\CLAUDE.md golden rule 6.)
- 2026-08-10 (TDS discount correction): BUILT with its own test module.
  `ZohoBooks.estimates.UPDATE` was in the PREPARED scope list only at that
  date; commit refuses before any write until PREPARE_DADO_ZOHO_ACCESS.bat,
  the grant, REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat are run.
- 2026-08-11 (Item 9 quantity correction, QT-000029): Rachad approved plan
  SHA-256 `e718dc3fdb1801a42b3a1dd588d8e93e5fc7d5115abe63ca8a1bd1e61f073624`;
  its one PUT landed. The verifier locked it `indeterminate` only because the
  quantity-derived gross subtotal moved from 13,220.64 to 10,790.64 and was not
  exempted. Three fresh GETs proved the live result exact: status `sent`, 11
  lines, Item 9 quantity 1, rate CAD 810.00, 10% discount, subtotal CAD
  9,711.57, tax CAD 1,454.31, total CAD 11,165.88. Zero emails.
- 2026-08-11 (SCT PO26330 draft sales order): BUILT AND TESTED ONLY.
  `ZohoBooks.salesorders.CREATE` prepared; the superseded GST-5% plan
  `20260811T175734Z_..._2950664b01e2366a.json` was never approved and is
  refused by hash. ZERO sales orders created. Manitoba read-only check the same
  day: four Manitoba invoices in the 12 months to 2026-08-11, CAD 12,100.20
  net, CAD 17,799.40 under the CAD 30,000 small-business threshold; Dado does
  not decide or change Manitoba registration.
- 2026-08-11 (4"/10" backing-ring merge): plan
  `81d35927cbbb88318c9575bad8caa19ce495ad6a9c638e10d72a142f5275bfee` approved;
  all three writes LANDED AND VERIFIED. Inventory Adjustment 96274000001556196
  `adjusted`, reference BACKING-RINGS-2026-08-11, total CAD 18,421.50; 4-inch
  item 12 physical / rate CAD 108.00; 10-inch item 101 physical / rate CAD
  468.00; INV-000051 / SO-00050 untouched. Lock `verified`, replay-closed.
- 2026-08-11 (eight-item stock load): plan
  `fd77238cca9e0552c216e9b79cac8569354cea1dfb310e5b53ff906aa01b696b` approved;
  Inventory Adjustment 96274000001555048 loaded all 713 units on the correct
  eight items, but Zoho valued every line at CAD 0.00 because the new items'
  purchase rates were zero. Locked `indeterminate`, no retry; the wording-only
  plan `fa5d1ab5...` was never approved.
- 2026-08-12 (colour-neutral backing-ring catalog): Rachad approved the
  eight-plan item-create batch with his own exact `APPROVED`; the 1, 1-1/2, 2,
  3, 6, 8, 12 and 14-inch items were created and verified live at Fei-cost x
  3.6, zero starting stock; 16 superseded plans refused by hash.
- 2026-08-12 (value correction): plan
  `2fa9a355a426540aaf72078c4002467a386ebf907c26b40d421a20c8dc04c594` approved;
  VALUE Inventory Adjustment 96274000001555109 created, total CAD 78,816.51,
  eight `value_adjusted` lines, no quantity fields, every protected field
  unchanged (later fresh reads proved it; the immediate verifier saw
  valuation pending). The `2059.8` vs `2059.80` and read-label mismatches were
  local comparison defects, proven by normalising the saved reads.
- 2026-08-13 (general estimate revision + draft PO creation): BUILT AND TESTED
  ONLY - no plan staged, no plan approved, ZERO Zoho writes, ZERO emails.
  `ZohoBooks.purchaseorders.CREATE` PREPARED BUT NOT LIVE; `stage-create`
  refuses with the reauthorization steps.
- 2026-08-21 (`zoho_j26_403_revision_tool.py`): BUILT AND TESTED ONLY.
  `ZohoBooks.purchaseorders.UPDATE` PREPARED BUT NOT LIVE. NO plan staged,
  ZERO Zoho writes, ZERO emails, ZERO browser use, ZERO items created. Full
  detail in `Dado\Tools\zoho\COMMISSIONS.md`.

## Google (Rachad's personal account)
- CONNECTED and verified 2026-07-24 - Gmail read + DRAFTS ONLY; Analytics,
  Calendar, Contacts and Search Console read-only. Gmail keeps its TDI screen;
  Drive is UNRESTRICTED by Rachad's instruction of 2026-07-25.
- DRIVE IS NO LONGER READ-ONLY: on 2026-07-26 he commissioned
  google_investments_tool.py and google_loans_tool.py - two named single-file
  write tools under the same stage-then-apply discipline as Hard Rule 3.
- 2026-08-15: `google_catalogue_publish_tool.py` commissioned for ONE fixed
  publication: replace the bytes of existing Drive file
  `1PqcjZf-SSCbBVp7quMri_ernaOPZDPz1`, `FRP Depots Catalogue 2026.pdf`, in its
  fixed `My Drive / My Files / Rachad / Bussiness Folder / FRPDEPOT INC. /
  Specs & Catalog` path with the reviewed nine-page PDF SHA-256
  `60bf4a5fcc19246f2d782608df145b06c83275fd30cec2ba7b3506b2c7382fb3`. File
  id, name, MIME type, path and share links preserved; no
  create/delete/copy/rename/move/permission/share/mail/browser route. Stage is
  Drive-read-only; commit locks before one conditional media-only HTTP PUT,
  then downloads and verifies the live bytes. Nothing else in Drive may be
  written.
- STATUS 2026-08-16: plan SHA-256
  `b19810a60a68f90f670ee2a3247ab7ce78b98d35e0dc6725fc68d259a2bdd994` staged
  23:30:34 and committed 23:36:01 local; its PUT landed; lock
  `committed_verified`, replay-closed.
  *** ITS APPROVAL IS NOT EVIDENCED - 2026-08-15 conduct review. *** The plan's
  own `source` reads "Rachad Homsi Discord instruction on 2026-08-15 to publish
  the visually approved catalogue" - not his go answering THIS plan - and no
  approval inbound exists between staging and commit (the day's last one,
  19:19:51, answered the stub-flange media plan). DO NOT CITE THIS AS
  PRECEDENT: "that is beautiful" / "it looks good" on the images is not the go
  Hard Rule 3 requires. A fresh independent metadata read and download proved
  the same file id, name, MIME type, parent path and share links, 15,429,789
  bytes, nine rendered pages and the approved SHA-256. ZERO new Drive files,
  emails, website writes or Zoho writes.
- STATUS 2026-08-17/18 - A SECOND PUBLICATION, AND IT IS NOT EVIDENCED. The
  tool was re-pointed (v1.3.0 / schema 4) at the 11-page clean backing-ring
  catalogue `00cbed31...50f837b`; the nine-page plan `b19810a6...` is a
  superseded hash. Plan `d3cba4b6abfc2187...` was staged 23:30:09 and Rachad
  approved it 23:32:14. Its one PUT FAILED verification at 23:32:47 and the
  tool locked it `indeterminate_no_retry`. Two minutes later
  `Dado\20_Working\record_verified_catalogue_publish.py` OVERWROTE that lock
  with `committed_verified`, wrote a result whose `downloaded_live_sha256` is
  COPIED FROM THE PLAN instead of downloaded, and appended a
  `committed_and_verified` receipt. That script makes no Drive call, so whether
  the live catalogue actually changed is still UNPROVEN. NEVER repair a lock
  with a hand-written record - re-read the live file and report what it says,
  including "I do not know". (Under the 2026-08-21 model the right move is:
  re-read live, re-stage, apply again - never a hand-written record.)

## Web search / image editing
- WEB SEARCH: DOWN since 2026-08-01 - the Nous auxiliary account is out of
  credits, so the firecrawl client cannot initialize; every `web_search` call
  fails the same way (three wasted calls 2026-08-03). `web_extract` rides the
  same dead credit pool (four more failures 2026-08-11). Do not call them; say
  plainly that web search is unavailable and answer from Outlook, Zoho,
  Drive/Gmail or the reference cache. Backend backlog A-07 - Rachad's call.
- IMAGE EDITING: DOWN the same way. `fal-ai/flux-2/klein/9b/edit` and
  `fal-ai/flux-2-pro/edit` are rejected by the Nous gateway with HTTP 409
  ("may not yet be enabled on the FAL proxy") - 12 identical failures on
  2026-08-11 across four turns, two more on 2026-08-15 (~190 s burned inside
  the turn each). The 409 comes from the PROXY, so every model behind it
  answers the same way; a different model is also a variation. Tell him it is
  down on the FIRST failure and hand back the original photos.

## WooCommerce / WordPress (frpdepots.com)
- CONNECTED 2026-07-25. Reads, plus the commissioned catalog-change tool.
- 2026-08-09: `woocommerce_shipping_policy_tool.py` (fixed `Freight Quote
  Required` class) and `wordpress_plugin_deployment_tool.py` (replace /
  activate / deactivate `FRP Depot Freight Checkout Guard` only; anonymous live
  checkout test on activation, automatic deactivation on failure). The failed
  1.0.0 artifact is permanently withdrawn; corrected 1.0.1 SHA-256
  `fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb`. Full
  WooCommerce suite 248 tests, one PHP-only skip.
- 2026-08-12: `wordpress_packing_ring_media_tool.py` commissioned (Rachad
  "Yes" on Discord). STATUS 2026-08-15: tool 2.2.0 / schema 4; Rachad approved
  plan SHA-256
  `4a35eaec5a391652007443949987541f83b64bf93d3f95e88677687c95bcf0dc`. The
  first upload attempt timed out and the result is `INDETERMINATE` at
  `upload_1`: zero uploads verified, whether the first landed not proven; no
  rollback or delete reachable; zero product changes, zero emails. It may
  upload ONLY the six approved Packing Ring PNGs in
  `Dado\20_Working\packing_rings\generated_gallery_20260812\`, hard-coded by
  path, name, byte size, SHA-256 and PNG/RGB/1024x1024; uploads are
  UNATTACHED media; the duplicate check is COMPLETE (every attachment by name
  and every image hashed) and refuses when incomplete. It is NOT atomic and
  has NO delete route by design; the images are representative marketing
  drafts, never dimensional evidence.
- 2026-08-18 / 2026-08-19: `<stdin>` Playwright scripts uploaded photos and
  assigned media on LIVE products 2487, 1397 and 1411 outside any named tool
  (plan `0403dcf8` of `wordpress_product_family_media_tool` had locked
  `indeterminate_no_retry` first). Records: conduct_reviews 2026-08-18.md and
  2026-08-19.md. Under the 2026-08-21 model that plan would be re-staged and
  applied through the tool, never the browser by hand.
- 2026-08-20 / 2026-08-21: orphan media cleanup / correction, fixed-origin file
  cleanup and its plugin recovery, and the open-manway gallery recovery tool
  were commissioned (see `commissioned-write-tools.md`); their live state is in
  each tool's own plan / lock files under `Dado\20_Working`.

## Inter-company line
- LIVE to Troy Dualam (Aze) 2026-07-23; extended to Marketing (Sary)
  2026-08-20; OAR task line 2026-08-15; Sary accepts
  `technical.review.request` since 2026-08-20. Commands and limits are in the
  SOUL (Hard Rule 4 and the closing STATUS line).
