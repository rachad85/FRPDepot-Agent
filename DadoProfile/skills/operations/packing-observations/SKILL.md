---
name: packing-observations
description: How FRP Depot replaces RESEARCHED packing estimates with real measured ones - the order monitor queues an opportunity when an order contains a product whose packing is still only estimated, and a physical measurement enters ONLY through packing_observation_tool.py record, from Rachad's own words, never from a scan or an inference. Load when the packing monitor speaks, when Rachad reports measurements from a packed order, or when a shipping weight or box size is questioned.
---

# Packing observations

`packing-order-monitor` runs `packing_order_monitor.py` - one read-only
WooCommerce scan per run. When an order contains a product whose packing figures
are only a **RESEARCHED estimate**, it prints one short line asking for the real
measurements. When nothing new is relevant it prints nothing at all, because a
monitor that speaks every run stops being read.

`packing-observation-weekly-reminder` nudges on Mondays at 09:00 for what is
still outstanding.

## The line that must never be crossed

**The monitor can queue an opportunity. It can NEVER record a measurement.**

Physical numbers enter through exactly one route:

```
C:\FRPDepot\Dado\Tools\woocommerce\packing_observation_tool.py record
```

and only **from Rachad's own words**. Not from a scan, not from a supplier
sheet, not from your own estimate, not from a similar product, and not from a
number a customer mentioned. If you did not get it from him, it is not a
measurement.

This matters because a researched estimate that quietly becomes "measured" can
never be told apart from a real one afterwards - and shipping quotes are built
on these numbers.

## The tool

```
packing_observation_tool.py initialize | scan | pending | show | report
                            record | correct | validate
```

- `pending` - what is still waiting on a real measurement
- `record` - his measurements, once, for a specific product
- `correct` - fixing a recorded value, still from his words
- `validate` - check the store before trusting it

## What to ask for

The monitor's own follow-up line is the exact list, and it is deliberate:

> actual package quantity, L x W x H cm, scale weight kg, packing material, and
> a photo/document reference.

Ask for all of it. A weight without dimensions does not price a shipment, and
dimensions without the packing material do not survive the next supplier
change. If he gives you part of it, record what he gave and say what is still
missing rather than filling the gap.

## Units are not optional

Centimetres and kilograms. If he says "about 14 pounds", convert it, show the
conversion, and let him confirm before recording - do not silently store
6.35 kg against a number he said in another unit.

## What this output never contains

No customer, address, amount, tax, payment or contact detail. The scan does not
hold any of it, by projection - keep it that way in whatever you write on top.
An order id and a product are the identifying detail; a customer name is not
needed to measure a box.

## Bounded output

`MAX_LINES = 12`, one line per order, newest last. If more orders qualify than
that, say so - "12 shown, N more queued" - rather than letting the cap look
like the whole picture.

## When he asks why a shipping figure looks wrong

Check first whether that product's packing is **researched or measured**. A bad
shipping quote on a researched product is expected behaviour and the fix is a
measurement; a bad quote on a measured one is a real defect worth investigating.
Say which one you are looking at before proposing anything.
