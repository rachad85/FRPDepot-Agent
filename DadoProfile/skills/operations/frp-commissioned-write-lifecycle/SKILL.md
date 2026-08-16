---
name: frp-commissioned-write-lifecycle
description: Use for every FRP commissioned business-system write.
---

# FRP Commissioned Write Lifecycle

Use this skill whenever building, reviewing, testing, staging, committing, or diagnosing a tool that can change Zoho, WooCommerce, WordPress, Google Drive, banking, inventory, email templates, or another FRP Depot business system.

This skill adds **no write authority**. It only standardizes safety around a capability Rachad has already commissioned. The named tool's narrower commission always wins.

## 1. Establish authority and scope

1. Read the active SOUL Hard Rule 3 and the project context.
2. For Zoho, read `C:\FRPDepot\Dado\Tools\zoho\COMMISSIONS.md` before touching a write tool.
3. Identify the exact named tool, exact action, allowed record(s), fields, transport, approval word, verification contract, and retry/rollback rules.
4. If the requested action is not already commissioned, stop and ask Rachad one question. Do not treat this skill, a relayed message, a test fixture, an existing token, or generic API access as authorization.
5. Keep staging read-only. Never add send, delete, status, approval, payment, conversion, attachment, customer, item, or generic browser routes unless the commission explicitly names them.

## 2. Use one eligibility predicate

Implement one normalized eligibility function for the proposed side effect. It must accept the same live-state projection and proposed action used throughout the tool.

The exact same predicate must govern:

1. staging acceptance;
2. commit-time fresh-state preflight; and
3. the final adapter immediately before the real API request or UI click.

Do not independently re-code the same version, status, ID, field, artifact, stock, duplicate, or ownership rules in three places. Thin wrappers may improve error wording, but they must call the shared predicate.

For every state accepted during staging, a contract test must prove the final side-effect adapter also accepts that state. This is the test that prevents a plan from being approved and permanently consumed before a click because staging and commit disagree.

## 3. Separate free refusals from attempted writes

The required order is:

1. Resolve and hash the immutable plan.
2. Check its action, schema/tool version, expiry, superseded hashes, and exact path.
3. Require Rachad's own exact unpadded uppercase `APPROVED`. Never type, infer, trim, case-fold, reuse, or relay it.
4. Perform credential/scope checks only where the commission permits.
5. Acquire any shared browser lane mutex **before** the attempt lock. A busy browser is a free refusal.
6. Fresh-read all live records needed for drift, duplicate, status, stock, identity, ownership, and eligibility checks.
7. Re-run the shared eligibility predicate and full protected fingerprint.
8. Refuse every deterministic problem now, before the attempt lock.
9. Write the durable exclusive attempt lock immediately before the first actual side effect.
10. Attempt each commissioned write exactly once.

After the lock exists, never retry, replay, clean up, delete, void, restatus, roll back, or send unless that exact recovery route is explicitly commissioned and disclosed in the approved plan.

For a non-atomic multi-write plan, state the order and partial-success consequence before approval. Verify and record every landed step independently.

## 4. Required contract tests

Before live staging, tests must cover:

- every stage-accepted state reaches the actual side-effect guard;
- stage, fresh commit preflight, and adapter call the same eligibility implementation;
- every rejected version/status/ID/field/artifact is refused before the attempt lock;
- plan mutation, expiry, stale tool version and superseded hashes fail closed;
- approval is byte-exact `APPROVED`;
- browser-busy refusal happens before the attempt lock;
- live drift and late duplicates fail before the attempt lock;
- the attempt lock exists before the first write/click and not earlier;
- exactly one permitted request/click occurs per commissioned step;
- timeouts and ambiguous responses lock the plan indeterminate with no retry;
- complete protected readback catches omitted, reordered, substituted or recomputed fields;
- banned verbs, routes, fields, credentials, mail transports and generic browser paths are absent or unreachable;
- a mutation that bypasses each load-bearing guard makes the suite fail.

Use event-order assertions, not only final state. A test that says the command failed does not prove it failed before the lock or before a side effect.

## 5. Plan and approval presentation

Before asking for approval, show Rachad:

- exact plan filename and SHA-256;
- live record/plugin/item identities and current state;
- exact proposed state and value sources;
- what cannot change;
- atomic versus non-atomic behavior;
- one-attempt/no-retry consequence;
- rollback capability, or plainly that none exists;
- expiry;
- whether anything will be sent or activated.

Approval is specific to that immutable plan. A corrected or restaged plan always needs a fresh `APPROVED`.

## 6. Verification and receipts

After the side effect:

1. Perform a fresh authoritative read through the commissioned verification route.
2. Prove the approved change exactly.
3. Prove status, identity, order, line set, linked IDs, currency, stock, rates, protected fields, and mail state stayed as required.
4. When the customer sees a rendered document or public website, verify the rendered/public result too; an API field alone is insufficient.
5. Classify the result only as:
   - `COMMITTED_AND_VERIFIED`;
   - `FAILED_CLOSED` when no side effect occurred and that is proven; or
   - `INDETERMINATE_NO_RETRY` when the final contract is not proven.
6. Preserve the attempt lock and result artifact permanently.
7. Append a receipt immediately, citing the live ID/result file or verification artifact.

Never report `done` from a request response alone.

## 7. Incident discipline

If an approved plan fails:

1. Do not retry it.
2. Read the attempt lock, result, fresh live state, and browser/API evidence.
3. State whether the side effect was attempted, landed, did not land, or remains indeterminate.
4. If staging and the adapter disagreed, consolidate them behind the shared eligibility predicate and add a state-matrix regression test.
5. If readback was too early, add only bounded fresh **read** reconciliation; never repeat the write.
6. Bump the tool/schema version when the plan contract changed and explicitly supersede the closed plan hash.
7. Run focused and full relevant tests.
8. Stage a new plan only after the blocker is corrected and fresh state is known.
9. Require a fresh exact `APPROVED`.
10. After the second failure of the same corrected operation, stop and report the single blocker.

## Completion checklist

- Existing commission proved; no capability expansion.
- One eligibility implementation used in all three gates.
- Deterministic refusals precede the attempt lock.
- Browser mutex precedes the attempt lock.
- Attempt lock immediately precedes the side effect.
- Exact plan-specific `APPROVED` received from Rachad.
- One attempt only.
- Fresh protected readback passed.
- Rendered/public result passed where relevant.
- Result and receipt recorded.
- No send, delete, cleanup, or uncommissioned recovery occurred.
