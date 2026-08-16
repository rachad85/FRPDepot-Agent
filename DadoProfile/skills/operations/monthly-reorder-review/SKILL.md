---
name: monthly-reorder-review
description: How Dado turns the monthly READ-ONLY Zoho reorder analysis into a buying recommendation for FRP Depot - what the four inputs actually mean (physical stock, invoice demand, open sales commitments, open PO quantities), why an item lands in order_candidates versus covered_by_incoming versus watch, the 4-month lead time the whole model rests on, and the fact that nothing here may write to Zoho. Load on the 1st-of-month reorder run, or whenever Rachad asks what to buy, what is running out, or whether stock covers commitments.
---

# Monthly reorder review

`dado-monthly-reorder-review` runs `zoho_reorder_analysis.py` on the 1st at
08:00. **This is the one scheduled job that runs your brain through the cron
agent** - the script's output lands in your prompt and you reason over it. The
other nine are deterministic scripts.

The analysis is **READ-ONLY. It never writes to Zoho.** It writes dated JSON and
CSV under `Dado\20_Working\reports` and appends one local receipt. Your job is
the recommendation, not the arithmetic.

## Completeness gate

Every Inventory item, invoice, Sales Order and Purchase Order collection must
finish with Zoho's explicit boolean `page_context.has_more_page == false`.
Missing/empty/non-boolean pagination metadata, or a page ceiling reached while
Zoho still reports more rows, aborts the report. Never interpret an absent page
context as "last page" and never reason from a partial reorder file as though it
covered the organization.

## The four inputs, and what each one is really telling you

| Input | What it means | How it misleads |
|---|---|---|
| Physical stock | What is on the shelf now | Says nothing about what is already promised |
| Invoice demand | What actually sold, over the history window | A single large invoice can look like a trend |
| Open sales commitments | Stock already spoken for | Easy to double-count against physical stock |
| Open purchase-order quantities | What is already on the water | The reason a scary-looking item may need nothing |

Physical stock alone is never the answer. An item can be well stocked and still
be short, because commitments have taken it; another can read empty and need no
action, because a PO covers it.

## The three buckets

- **`order_candidates`** - demand and cover say buy. These are the
  recommendation.
- **`covered_by_incoming`** - would have been a candidate, but open POs already
  cover it. **Do not re-order these.** Say they are covered and by how much.
- **`watch`** - moving, but not yet a decision. Name them so a pattern is
  visible next month.

Also in the report: `all_rows`, `counts`, `method`, and the window boundaries
(`history_start`, `recent_start`, `as_of`). Quote the window whenever you quote
a demand figure.

## The assumption everything rests on

**`lead_time_months: 4`.** Every cover calculation assumes four months from
order to shelf. If Rachad tells you a supplier is faster or slower, that is a
change to the model, not a note in your reply - say so plainly and stop, rather
than mentally adjusting the numbers yourself.

## What a good recommendation looks like

1. **The headline** - how many items need ordering this month, and whether that
   is normal against last month.
2. **Per item**: SKU, physical stock, committed, incoming, the demand figure
   with its window, and the quantity you suggest.
3. **What is covered and needs nothing** - short list, so he can see you checked
   rather than skipped.
4. **What you are unsure about**, named. Thin sales history is the usual cause;
   say "two invoices in the window, I would not trust this one" rather than
   producing a confident number from nothing.

Never invent a demand figure or a lead time. If the report does not carry it,
you do not have it.

## Where the boundary is

You produce a recommendation. **Buying is Rachad's**, and there is no route
from this analysis to a purchase order. Any Zoho write at all happens only
through the named commissioned tools with their own approval words - and none
of them creates a PO.

If he then asks you to act on it, check which tool covers the action before
saying yes. Most of what follows a reorder decision has no tool at all yet, and
"I have no route for that" is the correct answer.
