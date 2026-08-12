---
name: daily-banking-review
description: How Dado reads the FRP Depot imported bank feed and what she may do about it - the daily 08:15 review is GET-only and never stages or commits, rows are attributed by their OWN account_id so nothing lands under the wrong currency, and the only write route is zoho_banking_reconciliation_tool.py on an exact uppercase APPROVED. Load when Rachad asks about the bank feed, an unmatched or uncategorized line, a transfer with the wrong accounts, or anything the daily banking digest reported.
---

# Daily banking review

The cron `dado-daily-banking-review` runs `dado_daily_banking_review.py` at
08:15 every day. It is **GET-only. It never stages and never commits.** Its job
is to tell Rachad what the imported feed looks like, not to act on it.

This skill is what you bring when he asks about what it reported, or asks you
to fix something in the feed.

## Read the report before you reason about it

The script is silent when there is nothing worth saying. If he is asking, there
is either output to explain or a question the output raised. Go and read the
actual run rather than reconstructing it from memory.

## The attribution rule - the one that protects the money

A row is reviewed only when **its own `account_id`** is a configured record
**and** the feed's `account_name` agrees with the name read live from the Books
bank-account record. Everything else is **DISCLOSED, never dropped and never
mis-attributed**.

This exists because the earlier version attributed every returned row to the
account it had *asked* for. Softening that check without restructuring
attribution would turn an availability bug into a **money-attribution** bug -
USD build-up money reported under "Desjardins CAD".

So: if a row appears under "disclosed" rather than under an account, that is the
guard working, not a failure. Report it as "this row did not match a configured
account, here is what it says" - never quietly file it under your best guess.

## What you may actually change

One tool, and only through it:

```
C:\FRPDepot\Dado\Tools\zoho\zoho_banking_reconciliation_tool.py
```

It can do exactly this and nothing else:

- **match** and **unmatch** an imported bank line
- **categorize** and **uncategorize** an imported bank line
- **correct the source/destination account links on an EXISTING transfer**

It cannot create a transaction, delete one, move money, or touch anything
outside the imported feed. There is no other banking write route, and you do
not build one.

## The approval word here is exact

`APPROVAL_WORD = "APPROVED"` - **unpadded uppercase APPROVED and nothing else.**
Not "approved", not "yes go ahead", not "APPROVED!". This is deliberate and it
is not the Aze de-gating pattern: your gates are attached to specific ACTIONS on
specific FIELDS, which is why they read as smooth rather than obstructive. Do
not campaign to relax it.

Stage the exact change, show him what it will do, and wait for that word.

## What never appears in a banking message

Keys, tokens, passwords - Hard Rule 2, not even to check them. And nothing
about TDI: the data wall to `C:\AgentTeam` holds in both directions. The
conversation line to Aze exists; the money does not cross it.

## When the feed looks wrong rather than the categorization

Three different things get confused. Say which one you believe it is:

1. **A row that has not imported yet** - the bank has not delivered it. Nothing
   to fix here.
2. **A row imported under an unexpected account** - the attribution guard above.
   Report it, do not re-file it.
3. **A row matched or categorized incorrectly** - this is the one the
   reconciliation tool is for.

Never report "the feed is broken" without saying which of those you mean and
what you read that says so.
