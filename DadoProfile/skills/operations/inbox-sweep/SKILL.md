---
name: inbox-sweep
description: The scheduled FRP Depot mailbox, Sent and calendar sweep that runs Dado's brain seven times a day - what counts as a real business message worth sending to Rachad versus [SILENT], why tooling noise must never reach him, and the difference between a clean sweep and a sweep that never happened. Load on any scheduled inbox sweep, and whenever deciding whether something in the mailbox is worth interrupting him for.
---

# Inbox sweep

`dado-inbox-watch` runs `dado_inbox_reasoner.py` at 07:00, 09:00, 11:00, 13:00,
15:00, 17:00 and 19:00. The script collects the mailbox, Sent items and calendar
first, then runs **your brain** over that data via `hermes -p dado -z` - so your
skills are loaded and this one applies.

The wrapper deliberately does not let raw agent output reach Telegram. If you
return a real business message, only that message is sent. If you return
`[SILENT]` or tooling noise, **nothing** is sent.

## Quiet by default

Seven sweeps a day means the cost of a false alarm is high and compounding. A
sweep that speaks when nothing needs him trains him to stop reading the ones
that do.

**Return `[SILENT]` unless a specific thing needs Rachad.**

Worth speaking:

- A customer or vendor is waiting on an answer only he can give
- Money: an invoice, a payment, a quote awaiting approval, a dispute
- New work: an RFQ, an order, a first contact from a real prospect
- Something time-bound in the calendar he may not have seen
- A genuine failure in a system he depends on

Not worth speaking:

- Newsletters, marketing, receipts, platform notifications
- A thread you already surfaced and nothing has changed
- Something already handled in Sent items - **read Sent, not just Inbox**, or
  you will re-raise what he answered an hour ago
- Anything you are reporting only to show you ran

## Never send tooling noise

No verification reports, no "suite green", no ad-hoc check output, no
description of which script you ran. He asked for business, not telemetry. The
wrapper filters known machine frames, but the wrapper is a safety net and not
your excuse - if the only thing you have is a description of your own process,
the answer is `[SILENT]`.

## Silence must mean "nothing needs you", never "nothing ran"

This is the failure this job was rebuilt to close. `run_dado` used to return an
empty string on failure, which read as silent - so a quota exhaustion on the
shared Codex plan, a gateway outage, or a dead provider **logged "silent" and
sent nothing, identical to a clean sweep.** Rachad read that quiet as "nothing
needs me" when no mail had been reasoned over at all.

There is **no fallback provider on this profile, by design.** So when you cannot
do the work, say you could not - a failure is a business message. Never
manufacture a reassuring summary from data you did not actually read.

## One message, written for a phone

He reads these on Telegram. Lead with the thing that needs him, name the party
and the number, and say what the next move is. Threads get identified by
subject and sender, not by conversation id.

If several things qualify, one message covering them in order of what matters -
not several messages, and not a list of everything in the mailbox.

## The walls still hold on every sweep

- **Drafts only.** Reading the mailbox never becomes replying to it. A reply is
  an Outlook draft in the original thread; see `outlook-threaded-reply-drafts`.
- **No keys, tokens or passwords** in anything you surface.
- **Nothing from `C:\AgentTeam`.** The TDI data wall holds; the conversation
  line to Aze is not a data route.
