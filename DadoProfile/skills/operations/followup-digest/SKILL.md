---
name: followup-digest
description: The FRP Depot morning follow-up digest - every external thread Rachad spoke last in and is still waiting on, tiered by kind (RFQ/quote 5 business days, payment 7, general question 3), ordered by money and new work before age. Covers what counts as overdue, why a thread he spoke last in is NOT automatically handled, and the drafts-only line on every chase. Load when the digest lands, when he asks what is outstanding or who has not replied, or when he asks you to chase someone.
---

# Follow-up digest

`dado-followup-digest` runs `dado_followup_digest.py` at 08:00 Monday to
Friday. It reports the threads **Rachad is waiting on** - the ones where he
spoke last and nothing came back.

## Why this exists, and what it corrects

Until 2026-07-24 the watch was one-directional. It surfaced mail waiting on him
and treated any thread he spoke last in as "handled, never surface" - so a quote
or an RFQ he sent could go silent forever and nothing noticed.

What that was hiding, measured the day it was built: **15 overdue threads**,
including *"Quote QT-000023 is awaiting your approval"* silent for **42 business
days**, an RFQ at 20 days, and **CAD 4,101.30 outstanding at 9 days**.

So: **a thread he spoke last in is not a closed thread.** It is the most likely
place for money to go quiet.

## The tiers, as Rachad set them

| Kind | Overdue after |
|---|---|
| RFQ / quote | **5 business days** |
| Payment | **7 business days** |
| General question | **3 business days** |

Business days, not calendar days. The window scanned is 60 days back.

## Ordering

**Money and new work first** - payment and rfq_quote - **then by age.** Not
strictly oldest-first: a 6-day unpaid invoice outranks a 20-day general
question, because one is money and the other is conversation.

One line per thread. This is a digest, not a mail archive.

## Reading it well

For each item say: what it is, how long it has been silent, and what the next
move is. Distinguish clearly between:

- **He is waiting on them** - a chase is the move
- **They are waiting on him** - a chase is not the move; he is the blocker and
  should be told so plainly
- **Neither** - closed, superseded, or answered on another channel. Say so and
  stop surfacing it

The third case is the one that erodes trust in the digest. If an item is done,
say it is done rather than carrying it forward silently.

## Chasing is drafting

Hard Rule 1: **drafts only.** You never send, on any channel, to any client or
vendor. A chase means an Outlook **draft** in the original thread - use the
`outlook-threaded-reply-drafts` skill for that, including `source_match` rather
than hand-carrying a Graph message id.

Write the draft as a nudge with the specific thing attached: the quote number,
the invoice, the question. "Following up on the below" is not a chase, it is
noise, and it teaches the customer that your follow-ups are ignorable.

## What never goes in a chase

- Keys, tokens, passwords (Hard Rule 2)
- Anything from `C:\AgentTeam` - the TDI data wall holds both ways
- A payment amount you have not read this run. Never invent a figure

## When the digest is empty

Say nothing. A digest that speaks every morning stops being read, which is the
whole reason the script is silent when clean.
