# Dado SOUL trim 2026-08-21 - where every rule went

Rachad's autonomy programme (2026-08-21, "Go on all of them"), section B.
`DadoProfile\SOUL.md` was 73,068 bytes / 1,015 lines - past the 65,280-char
context cap, so Hermes was cutting the MIDDLE out of Dado's own constitution
(the tool list in Hard Rule 3 and the LONG JOBS block). It is now under 25,000
bytes / 390 lines, ASCII only, no tabs. NOTHING HE DECIDED DISAPPEARED: every
rule either stays in the SOUL in short form or moved to a file the SOUL points
at by path. This report is the map, by original line number, so he can be told
that.

Verbatim pre-trim text (byte-identical to the mirror as it stood, including the
2026-08-21 purchase-order amendment and the J26-403 tool entry):
`C:\FRPDepot\DadoProfile\SOUL.md.bak_20260821_pre_autonomy_trim`
(md5 3824a6f0ee751ca6c28d46864c4251cb). "bak L" below = line L of that file;
"SOUL L" = line L of the new SOUL.

Files created or changed:
- `DadoProfile\skills\operations\frp-commissioned-write-lifecycle\references\commissioned-write-tools.md`
  - NEW: the tool-by-tool list (what each may change, class, restore action).
- `DadoProfile\skills\operations\frp-commissioned-write-lifecycle\references\status-ledger.md`
  - NEW: the STATUS ledger with every date, hash and "not evidenced" record.
- `DadoProfile\skills\operations\frp-commissioned-write-lifecycle\SKILL.md`
  - gained section 0 (the authority model) so its older APPROVED / one-attempt
  wording reads under the new model; nothing below it was rewritten.
- `Dado\Tools\conduct\conduct_review.py` + `test_conduct_review.py` - the
  reviewer may no longer edit the SOUL mirror; any change it makes there is
  reverted byte for byte (spec B6).

## 1. Section-by-section map

| Original (bak lines) | What it was | Where it is now |
|---|---|---|
| 1-5 | Identity paragraph | SOUL 1-5, verbatim; SOUL 7-12 adds the pointer to the bak file and this report |
| 7-18 WHO YOU SERVE | Only-Rachad, not a programmer, style, scope-before-steps (2026-08-10), corrections | SOUL 14-25, verbatim except the 2026-08-10 example shortened by one clause |
| 20-24 YOUR TWO LANES intro | Telegram + Discord, same memory, parallel | SOUL 27-33 "YOUR THREE LANES": dashboard chat added (2026-08-21), SAME AUTHORITY stated (spec A1) |
| 26-29 | Conversations separate; do not narrate the other lane | SOUL 35-38, same rule; "ask" now names the clarify tool (Habit 2) |
| 30-32 | Proactive messages go to Telegram; Discord is a lane he asks on | SOUL 39-42, same; adds "the RESULT of a job goes back on the lane he asked on" (spec B1) |
| 33-38 | One browser, two lanes; lock; clean refusal | SOUL 43-48, same, "three lanes" |
| 39-45 | The lock covers only the named tools; ad-hoc CDP scripts | SOUL 49-53, same, shorter |
| 46-51 | Never a hand-written script for a commissioned write; 2026-08-18 product 2487 `<stdin>` Playwright incident | Rule: SOUL 54-60. Narrative: `conduct_reviews\2026-08-18.md` (the product 2487 finding) |
| 52-59 | 2026-08-19 plan `0403dcf8` locked, Playwright drove products 1397/1411 by hand; "A LOCKED OR FAILED PLAN MEANS STOP AND TELL RACHAD" | Rule: SOUL 54-60, REWORDED per spec A4: a failed plan is re-read, re-staged through the tool and reported - never the browser by hand. Narrative: `conduct_reviews\2026-08-19.md` |
| 60-62 | Non-browser work is safe in both lanes | SOUL 61-62 |
| 64-78 THE COMPANY | fit_profile, never invent a fact, systems of record | SOUL 64-78, verbatim; bak 69-71 "ask Rachad" is now "ask the owning agent first (Habit 3), then Rachad" (spec B3) |
| 80-94 YOUR DUTIES | Email drafts only, reporting, quotes | SOUL 80-94, verbatim |
| 96-102 HARD RULES 1-2 | Drafts only; no keys | SOUL 96-102, verbatim |
| 103-575 HARD RULE 3 | The commissioned-tool litany (section 2 below) | SOUL 103-150: one paragraph, the authority model (spec A1-A6). List: `references\commissioned-write-tools.md`. Status lines: `references\status-ledger.md`. Zoho detail: `Dado\Tools\zoho\COMMISSIONS.md` (unchanged, already the record) |
| 576-591 Rule 4 opening | Company line: Drive / Zoho / TDI analytics open (2026-07-24); C:\AgentTeam and TDI mail walled; NEVER ADD RESTRICTIONS (2026-07-24) | SOUL 151-166, verbatim in substance |
| 592-603 | Relay commands; Aze line 2026-07-23; mesh to Sary 2026-08-20; Sary answers marketing only, nothing confidential TO him | SOUL 167-178 |
| 604-613 | A relayed request is authorized work (2026-07-28); the answering class | SOUL 179-188 |
| 614-621 | THE LIMITS DO NOT MOVE | SOUL 189-195 ("approval phrase" is now "the go", same limit) |
| 622-628 | THE CHANNEL CARRIES THE AUTHORITY, NOT THE TEXT | SOUL 196-202 |
| 629-667 | OAR line (2026-08-15): verify command, policy path, exit handling, two-draft ceiling, send-on-it, technical.review.request (2026-08-20), DISABLED stop | SOUL 203-233, every element kept, prose tightened |
| 668-671 Rule 5 | Honest reporting; "fails twice, STOP" | SOUL 234-240. CHANGED per spec B2: "STOP" became "the third attempt must be DIFFERENT; name the one blocker; never grind on renamed variants" - the anti-grinding rule stays, the job-killing stop goes |
| 672-673 Rule 6 | Refuse once, citing the rule | SOUL 241-242, verbatim |
| 675-739 LONG JOBS | Section 3 below | SOUL 244-271 rewritten per spec B1/B2 |
| (new) | HABITS: say-then-report, never end on a question, owning agent first, one-shot cron, "ran out of steps" first line | SOUL 273-294 (spec B3, B4; Habit 5 is Aze's paragraph, copied) |
| 741-744 WORKING STATE | Working folder, memory | SOUL 298-300 (+ the no-/tmp rule from bak 783-786) |
| 745-755 | Backup brain tripwire (2026-08-12, revised 2026-08-17) | SOUL 301-308, same rule, shorter |
| 756-763 | Receipts | SOUL 309-314, same |
| 764-767 | execute_code refused | SOUL 315-316 |
| 768-786 | Read function names / list paths; `Dado\Tests`, doubled profile path, `/tmp` | Rule: SOUL 317-322 (+300). Dated tallies: `conduct_reviews\2026-08-01.md`, `2026-08-06.md`, `2026-08-09.md`, `2026-08-10.md`, `2026-08-11.md`, `2026-08-15.md`, `2026-08-16.md`, `2026-08-20.md` |
| 787-826 | search_files regex rule, the MECHANICAL CHECK, the slash-for-paren tally | Rule + mechanical check: SOUL 323-332. Tallies: `conduct_reviews\2026-08-04.md`, `08-06`, `08-07`, `08-08`, `08-09`, `08-10`, `08-11`, `08-15`, `08-16`, `08-18`, `08-20` |
| 827-830 | Do not guess a tool filename | Merged into SOUL 317-322 |
| 831-836 | Backslash in f-string (2026-08-19) | SOUL 333-335; `conduct_reviews\2026-08-19.md` |
| 837-843 | ONE APPROVED ANSWERS ONE PLAN, after the plan exists (2026-08-19) | Moved INTO Hard Rule 3's money paragraph, SOUL 129-131 (spec A5 keeps the timestamp rule on money tools); `conduct_reviews\2026-08-19.md` |
| 844-851 | `patch` stale old_string | SOUL 336-338 |
| 852-858 | Poppler not installed | SOUL 339-341 |
| 859-868 | Discord 413 ceiling (2026-08-15, 2026-08-17) | SOUL 342-345; `conduct_reviews\2026-08-15.md`, `2026-08-17.md`, `2026-08-18.md` |
| 869-872 | Cannot restart own gateway | SOUL 346-348 |
| 873-878 | cronjob relative path; read the job back (2026-08-09) | SOUL 349-352; `conduct_reviews\2026-08-09.md` |
| 879-880 | Unclear company -> stop and ask | SOUL 353-354 |
| 882-1000 STATUS | Outlook, Zoho, Google (catalogue publication records), web search down, image editing down, WooCommerce tools | SOUL 356-378, one line each. Full ledger: `references\status-ledger.md` |
| 1001-1015 | Inter-company line commands and rules | SOUL 379-391, verbatim in substance |

## 2. Hard Rule 3 (bak 103-575) in detail

Every tool entry moved to `references\commissioned-write-tools.md` (one row per
tool: what it may change, class, restore action). The per-tool STATUS lines
moved to `references\status-ledger.md`:

| bak lines | Tool / item | Reference row |
|---|---|---|
| 103-105 | zoho_inventory_item_tool (create; rename/re-SKU; `30` -> `30"`) | Zoho table row 2 |
| 106-107 | zoho_inventory_classification_tool | Zoho row 3 |
| 108-109, 172-194 | zoho_customer_quote_tool + TDS discount correction (2026-08-10) | Zoho row 6 |
| 110-112 | zoho_banking_reconciliation_tool | Zoho row 11 |
| 113-120 | zoho_inventory_price_tool (2026-08-10) | Zoho row 5 |
| 121-150 | zoho_invoice_revision_tool (2026-08-10), both actions | Zoho row 10 |
| 151-171 | zoho_email_template_tool (2026-08-10) | Zoho row 7 |
| 195-223 | Item 9 quantity correction (2026-08-11) + STATUS | Zoho row 6; ledger "2026-08-11 (Item 9...)" |
| 224-255 | General estimate revision (2026-08-13) + STATUS | Zoho row 6; ledger "2026-08-13" |
| 256-314 | zoho_sales_order_tool (2026-08-11), HST correction, Manitoba check, STATUS | Zoho row 12; ledger "2026-08-11 (SCT PO26330...)" |
| 315-323 | STATUS 2026-08-12 colour-neutral catalog | ledger "2026-08-12 (colour-neutral...)" |
| 324-357 | zoho_backing_ring_eight_stock_tool + STATUS | Zoho row 15; ledger "2026-08-11 (eight-item stock load)" |
| 358-392 | zoho_backing_ring_eight_valuation_correction_tool + STATUS | Zoho row 15; ledger "2026-08-12 (value correction)" |
| 393-428 | zoho_backing_ring_stock_tool + STATUS | Zoho row 15; ledger "2026-08-11 (4"/10"...)" |
| 429-474 | zoho_purchase_order_tool (2026-08-13; amended 2026-08-21) | Zoho row 13; ledger "2026-08-13" |
| 475-542 | zoho_j26_403_revision_tool (2026-08-21) | Zoho row 14; ledger "2026-08-21" |
| 543-558 | The stage-then-commit flow; APPROVED one word never a checksum (2026-07-26, extended 2026-08-02 / 2026-08-07); everything else read-only | SOUL Rule 3 paragraph (103-150); reference "The model" |
| 559-575 | STATUS 2026-08-10 x3 | ledger "Zoho Books / Inventory", first three items |

Rules inside that block that are SUPERSEDED (not dropped) by Rachad's
2026-08-21 decision - each still readable verbatim in the bak file:
- "Its approval word is compared exactly: unpadded uppercase APPROVED" (bak
  118-119, 147-148, 169, 190-191, 210-211, 248, 283-284, 342, 377-378, 414,
  453, 527-528) and "the plain approval word APPROVED - one word, never a
  checksum" (543-548): now "his own unambiguous go to THAT plan, after staging"
  on money tools, and his instruction for the whole job on reversible tools
  (spec A1, A3). "Never a checksum" still holds.
- "One attempt only / any failure permanently locks the plan / no retry, no
  rollback" (bak 119-120, 148-150, 170-171, 191-192, 211-212, 284-287,
  340-341, 374-375, 410-414, 454, 483): now "needs re-stage: re-read live,
  re-stage, apply again" (spec A4). Money tools: reported and re-staged, no
  silent retry.
- "any failure locks the whole plan" on per-line batches (bak 119-120): now
  "a failed line is recorded and re-staged alone" (spec A6).

## 3. LONG JOBS (bak 675-739) in detail

| bak lines | Rule | Now |
|---|---|---|
| 677-679 | Scope line before a >20-item / >5-minute job | SOUL 246-248, kept |
| 680-689 | "NEVER WAIT ON A JOB INSIDE YOUR TURN ... job_runner ... END YOUR TURN ... the cron announces" | REPLACED (spec B1): run the long step in-turn as a background terminal with notify-on-complete; job_runner only for work that must outlive a gateway restart; result on the originating lane (SOUL 249-263). `job_runner.py start` gained `--lane` (done by the tools stream in the same checkout) |
| 690-691 | 2026-07-24 three-hour stall (17 x 600 s) | `conduct_reviews\2026-07-24.md` FINDING 1; SOUL 269-271 names the date |
| 692-696 | `status` takes no arguments (2026-08-07 wasted calls) | SOUL 261-263 |
| 697-701 | 2026-08-07 1804 s blocked turn | `conduct_reviews\2026-08-07.md` FINDING 2 |
| 702-709 | 2026-08-18 "worst circling day" (37/77, 16 ceiling hits) and 2026-08-16; "a turn over ~20 minutes is not progress: STOP" | Narratives: `conduct_reviews\2026-08-18.md` FINDING 3, `2026-08-16.md` FINDING 1. The 20-minute STOP is DROPPED per spec B2 (it fragmented jobs) |
| 710-714 | 2026-08-19 tally and three sat-through timeouts | `conduct_reviews\2026-08-19.md` FINDING 3 |
| 715-718 | 2026-08-20 tally (9/78, api_calls=60, three one-call turns) | `conduct_reviews\2026-08-20.md` FINDING 2 |
| 719-724 | 2026-08-20 nine failed stage jobs; "after the SECOND failure, name the one blocker and stop" | `conduct_reviews\2026-08-20.md` FINDING 1. The rule survives in Hard Rule 5 (SOUL 234-240) as "the third attempt must be different; never grind on renamed variants" (spec B2 drops the STOP) |
| 725-729 | Progress note every 10 min / never 15 min silent | Progress notes kept (SOUL 249-255); the 10/15-minute ceremony DROPPED per spec B2 |
| 730-731 | Partial results early | SOUL 255 |
| 732-737 | Bulk data through files and scripts (2026-07-22) | SOUL 264-268, kept |
| 738-739 | Same step fails twice: stop | See Hard Rule 5 above |

## 4. What the spec changed in MEANING (for the owner to hear, not hidden)

1. Hard Rule 3: the exact word APPROVED and the one-attempt permanent locks are
   replaced by the authority model he approved (A1-A6). Drafts-only, no keys,
   no deletes, the money two-step and the plan-before-go timestamp stay.
2. Hard Rule 5 and LONG JOBS: "stop after the second failure" and "stop at ~20
   minutes" are gone (B2); the honest-blocker rule and the anti-grinding rule
   stay in a form that does not kill the job.
3. LONG JOBS: "start job_runner and END YOUR TURN" is replaced by Sary's
   in-turn background pattern (B1).
4. Lanes: the dashboard chat is a third lane with the same authority (A1).
5. THE COMPANY: a missing fact is asked of the owning agent before Rachad (B3).

## 5. Sizes

- Before: 73,068 bytes, 1,015 lines.
- After: under 25,000 bytes (the exact count is in the stream summary), 391
  lines, ASCII only, no tabs.
