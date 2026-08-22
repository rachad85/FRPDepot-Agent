# FRP Depot WooCommerce connector

Commissioned by Rachad Homsi on 2026-07-24.

## Capability

- Read products, variations, categories, attributes, settings, system status,
  shipping methods, payment gateways, customers, and orders.
- Public website crawl without credentials.
- Create/update products and variations only through the named staged-change tool.
  Every creation is forced to **draft**.
- No delete capability.
- No customer, order, payment, refund, coupon, webhook, plugin/theme, user, or
  arbitrary-setting writes.

## Credential rule

Never paste the Consumer Key or Consumer Secret into Telegram, email, a command
line, a JSON file, or the Git repository. Enter both only in the hidden prompts
opened by `C:\FRPDepot\CONNECT_DADO_WOOCOMMERCE.bat`.

The full connection is DPAPI-encrypted at:

`%LOCALAPPDATA%\FRPDepot-WooCommerce\connection.dpapi`

The folder is restricted to the current Windows user and SYSTEM. Credentials are
sent only in an HTTPS Authorization header to the exact origin
`https://frpdepots.com:443`. Redirects and query-string authentication are refused
so credentials cannot be forwarded or written into server/proxy logs.

A WooCommerce Read/Write key is broadly privileged outside this connector. It must
belong to a dedicated integration user, not Rachad's personal administrator user.
Revoke the key immediately if the server account is suspected compromised.

## Rachad's one-time connection steps

1. Sign in to `frpdepots.com/wp-admin`.
2. Open **WooCommerce > Settings > Advanced > REST API**.
3. Select **Add key**.
4. Description: `Dado FRP Depot Read-Write Approved Changes`.
5. Select a dedicated audit/integration user with the required WooCommerce access.
6. Permissions: **Read/Write**.
7. Select **Generate API key** and leave the page open.
8. On the server, double-click `C:\FRPDepot\CONNECT_DADO_WOOCOMMERCE.bat`.
9. Paste the Consumer Key and Consumer Secret into the hidden prompts.
10. Type `READ/WRITE` when the button asks you to confirm the key permission.
11. Close the WooCommerce key page after the button reports VERIFIED.

The connection check performs GET requests only. It does not test a write.

## Audits

- Public audit button: `C:\FRPDepot\RUN_DADO_WOOCOMMERCE_PUBLIC_AUDIT.bat`
- Private store audit button: `C:\FRPDepot\RUN_DADO_WOOCOMMERCE_STORE_AUDIT.bat`

Both start detached jobs and return immediately. The Dado job watcher reports
completion/failure to Rachad.

Private reports are stored outside Git under:

`%LOCALAPPDATA%\FRPDepot-WooCommerce\audits\`

Customer/order privacy uses positive API field projections: names, emails,
usernames, addresses, phones, IPs, user agents, notes, transaction IDs, coupon
codes, metadata, and payment details are never requested. Raw permitted records are
aggregated in memory and never cached. Unique guest customers are not estimated
because doing so would require identifying data.

## Write process

1. Dado prepares one JSON request with the action, exact fields, and a source for
   every field.
2. Dado runs `woocommerce_change_tool.py stage --input <file>`.
3. The tool reads the current resource when relevant, writes a hashed plan with a
   nonce and 24-hour expiry, and prints an action-specific approval phrase carrying
   the complete 64-character SHA-256 digest.
4. Dado shows Rachad the complete before/after plan.
5. Nothing happens unless Rachad himself returns the exact phrase for that digest.
6. Dado commits with the named tool; it rechecks the hash, expiry, payload, origin,
   route, SKU uniqueness, and current live record, then locks the plan against
   replay before performing one allowlisted POST/PUT. It reads the resource back
   and writes a receipt.

Approval for one digest cannot authorize any other plan. Changed, expired, stale,
or replayed plans are refused. An uncertain POST/PUT is locked as indeterminate and
is never retried automatically. The tool never creates an approval phrase on
Rachad's behalf.

## Allowed write actions

- `product_create`
- `product_update`
- `variation_create`
- `variation_update`

Initial product fields: `name`, `type` (`simple` or `variable`), `sku`,
`description`, `short_description`, `regular_price`, existing category IDs, and
closed global-attribute objects. Variation fields: `sku`, `description`,
`regular_price`, and closed global-attribute selections. Publication, visibility,
sale, stock/backorder, metadata, image/source URL, download, tax, shipping, and
external-product fields are refused. Unsupported fields/endpoints fail closed.
DELETE is not implemented.

## Shipping policy (freight quote)

Commissioned by Rachad Homsi on 2026-08-09. Commissioning authorises building and
testing. It is **not** approval of any store change.

### Capability

`woocommerce_shipping_policy_tool.py` is a separate stage/commit tool with exactly
three actions and one writable field:

- `shipping_class_create` - `POST /products/shipping_classes`. Name and slug are
  hard-coded to `Freight Quote Required` / `freight-quote-required`. The caller
  supplies neither, so no other class, title or slug can ever be created.
- `shipping_class_assign` - `PUT` on each explicitly enumerated product/variation,
  payload exactly `{"shipping_class": "freight-quote-required"}`.
- `shipping_class_remove` - the same routes, payload exactly
  `{"shipping_class": ""}`.

A plan enumerates every target by exact ID; nothing is ever selected by query,
category or search. Maximum 200 enumerated targets per plan. There is no DELETE,
no bulk/batch route, and no order, customer, payment, refund, coupon, webhook,
user, theme, plugin, setting, stock, price or catalog-copy write.

### Stage then commit

1. Dado writes one JSON input: `action`, explicit `targets`, and `sources.policy`.
2. `woocommerce_shipping_policy_tool.py stage --input <file>` reads each target
   live, records its current shipping class, its fingerprints and its metadata
   projection (below), and writes a hashed plan with a nonce and 24-hour expiry
   into `Dado\20_Working\woocommerce_shipping_plans\`. Staging writes nothing to
   the store and reports `external_write_performed: false`.
3. Rachad sees the complete before/after list and replies with the one word
   `APPROVED`. The tool never produces that word on his behalf.
4. `commit --plan <path> --approval APPROVED` re-verifies hash, expiry, schema,
   tool version, origin, action, route, payload and every target shape; refuses a
   key not declared Read/Write; refuses an origin that is not
   `https://frpdepots.com:443`; creates an exclusive replay lock **before** the
   first write; then, per target, performs one fresh read, one allowlisted `PUT`,
   and one read-back.

**The approval is compared exactly.** Only the string `APPROVED` is accepted -
not `approved`, not `Approved`, not ` APPROVED `, not `APPROVED.`, not a quoted
copy. Every other value is refused before the vault is opened and before any
network access. The earlier check stripped and case-folded first, which let an
echoed or auto-completed value stand in for a decision Rachad made.

The read-back asserts the shipping class is exactly the approved slug **and** that
a fingerprint over `attributes, backorders, catalog_visibility, categories,
cross_sell_ids, date_on_sale_*, description, dimensions, downloadable, downloads,
external_url, featured, images, manage_stock, menu_order, meta_data, name,
parent_id, price, purchasable, regular_price, reviews_allowed, sale_price,
short_description, sku, slug, status, stock_quantity, stock_status, tags,
tax_class, tax_status, type, upsell_ids, virtual, weight` is byte-identical before
and after. Anything else moved means the plan is locked `indeterminate` and never
retried.

**Known strictness:** that protected set includes `meta_data` and `price`. If a
third-party plugin touches either during a `shipping_class` save, the commit will
abort as indeterminate even though the assignment itself succeeded. That is
deliberate fail-closed behaviour. The remedy is to reconcile in WooCommerce and
stage a new plan, not to widen the tool. Schema 3 adds exactly one bounded
exception - a read-only wait for the single proven Google Listings & Ads sync
transient, which still succeeds only if the complete staged state returns. It
never accepts a changed state.

A plan can enter commit at most once. Approval for one plan authorises no other.
Expired, changed, stale, replayed or externally-modified plans are refused.

### Plan schema 2 - per-field protected diagnostics (2026-08-09)

On 2026-08-09 the first approved assignment plan
(`…7f8cedab6416e483`, 34 FRP Pipe variations) stopped at its **first** target:

    /products/1455/variations/1456 changed a protected product field.
    Reconcile this resource.

That was the correct refusal, and it was useless as evidence. Schema 1 stored one
**aggregate** hash over all 37 protected fields, so the tool could say *something*
moved and never *what*. Read-only reconciliation afterwards proved the assignment
itself had landed - variation 1456, SKU `PIDN25150PSI411`, now carries
`freight-quote-required` - and that its protected state is stable across fresh
reads but no longer matches the staging fingerprint. **Which field moved is not
recoverable from that plan and never will be**; nothing in it holds per-field
evidence.

Schema 2 exists so the next occurrence answers the question.

- `SCHEMA_VERSION = 2`, `TOOL_VERSION = "2.0.0"`. Both are written into the plan
  and re-checked at commit.
- **Every schema-1 plan is permanently dead.** It is refused before any vault or
  network access, and rehashing it does not revive it. Existing commit locks
  remain authoritative and are not disturbed.
- Each assignment target now carries, on top of its identity, stale fields and the
  still-mandatory aggregate `before_protected_fingerprint`:
  - `before_protected_field_fingerprints` - a **closed** mapping with exactly one
    full 64-character SHA-256 per protected field name, no more and no fewer;
  - `before_meta_data_projection` - the closed per-entry metadata projection below.

**Metadata projection.** `meta_data` is the field a third-party save hook is most
likely to move and the one an aggregate hash is least able to explain, so it gets
a per-entry projection. Each entry contributes exactly: its original list
`index`, its numeric `id` (or `null`), its `key`, and SHA-256s of the key, the
value and the whole entry. **Values are never stored, only hashed** - a value is
where a licence key, order reference or customer note would live. Key *names* are
kept in clear on purpose: they are WordPress field names such as
`_wc_facebook_sync_enabled`, and "some metadata entry changed" is not something
Rachad can act on. A key that is not printable text is withheld and identified by
its hash alone. Duplicate id/key pairs stay separate rows and malformed entries
stay representable as `shape: "malformed"` - a collapsed projection could hide the
very change it exists to find.

Limits, all fail-closed - an over-limit response is **refused, never silently
truncated**: 250 metadata entries per resource, 190 characters per key, 256 KiB of
canonical JSON per entry. Diagnostic lists are capped at 25 rows each and report
what they omitted, so a hostile response cannot inflate a plan, a lock or a
receipt.

**Sequencing.** Nothing here relaxes anything; the extra checks only add refusals.

| | |
|---|---|
| Stage | read live; record aggregate + per-field + metadata projection |
| Pre-write | fresh read; stale fingerprint and `date_modified_gmt`; aggregate; **every** per-field hash; metadata projection exactly - any mismatch refuses **before** the `PUT` |
| Write | one `{"shipping_class": …}` `PUT`, after the replay lock |
| Post-write | fresh read; shipping class exact; aggregate; every per-field hash; metadata projection exactly |
| Converge (schema 3) | only for the one exact Google-sync transient: bounded read-only re-reads until the complete staged state returns, or the plan locks |

A post-write mismatch names the exact field(s) - `… protected readback mismatch:
meta_data. Reconcile this resource.` - locks the plan `indeterminate`, and stops
the loop, so **no later target is written**. The attempted target is never
auto-rolled-back and never retried. A mismatch is never accepted or normalised.

**What a lock, a receipt and an error may say.** Field names, metadata keys,
indexes, ids, counts and SHA-256s. Nothing else: no raw product value, no page
text, no credential, no header, no request body, no exception dump. A transport
failure records only its class, HTTP status and REST code, because `WooError`
carries up to 1000 characters of the server's response body; any other unexpected
exception is reduced to its class name.

**One-target plans.** A plan with exactly one target is flagged
`diagnostic_scope: true` in the staging preview. That is a **preview flag only**:
it is the ordinary `shipping_class_assign` action writing the ordinary fixed
payload, it still needs Rachad's own exact `APPROVED`, and it is not a test-only
mutation. Its value is that a protected-field mismatch can be attributed to one
resource before the rest of the catalog is touched.

### Plan schema 3 - bounded Google-sync convergence (2026-08-09)

The schema-2 diagnostic answered the question it was built for. One approved
one-target assignment on `/products/1455/variations/2056` (SKU `PIDN12150PSI411`)
produced the exact cause:

- the intended shipping class **landed**;
- `changed_protected_fields` was `["meta_data"]` and nothing else;
- the metadata projection moved by **one entry**: id `63040`, key
  `_wc_gla_sync_status` - the Google Listings & Ads sync flag;
- same entry count, same identity, same index, same order, nothing added or
  removed;
- by canonical SHA-256, its value went staged -> transient at the immediate
  readback, and two later fresh reads found it back at the staged value, stable.

So the schema-2 refusal was correct and the state was *temporary*. Schema 3 waits
for that one transition and for nothing else.

- `SCHEMA_VERSION = 3`, `TOOL_VERSION = "3.0.0"`. **Schema-1 and schema-2 plans
  are both permanently dead** - refused before any vault or network access, and
  rehashing one does not revive it. Existing commit locks stay authoritative.

**The contract is closed and fixed by source.** Every plan core carries
`convergence_contract`, and no request field can supply, widen or replace it:

| field | fixed value |
|---|---|
| `kind` | `gla_sync_pending_to_synced` |
| `meta_key` | `_wc_gla_sync_status` |
| `staged_value_sha256` | `bed425ac…b875c8c` |
| `transient_value_sha256` | `12adac54…4ed67d36` |
| `schedule_seconds` | `[2, 4, 8, 16, 30, 30]` |
| `max_seconds` | `90` (the exact schedule total) |
| `final_requirement` | `exact_staged_protected_state` |
| `allowed_changed_protected_fields_during_wait` | `["meta_data"]` |

It is hashed into the plan, so an unhashed edit fails the digest check; a
**rehashed** edit to any field, value, type or schedule order is refused
semantically before the vault is opened. `2.0` is not `2`, and a reordered
schedule is not the schedule.

**Stage eligibility.** Each assignment target now carries
`gla_convergence_eligible`, decided from the value-free projection alone:

- **true** - exactly one sound `_wc_gla_sync_status` entry, already at the staged
  value digest. Only such a target may ever use the bounded wait.
- **false** - the key is absent. An ordinary resource; the wait never applies.
- **refused at staging** - the key appears more than once, is malformed, or holds
  any other value including the transient one. Rachad is never asked to approve a
  plan whose before-state is a sync in flight.

The flag is **never trusted as stored**: commit re-derives it from the plan's own
projection, so a rehashed plan cannot flip a resource into the wait.

**The exact transient detector** is a pure function, and every condition must
hold: the readback class already equals the approved class; the changed protected
fields are exactly `["meta_data"]`; the target was staged eligible; entry counts
identical; nothing added, removed, re-identified or reordered; exactly one
value-changed entry; same index and same numeric id; key exactly
`_wc_gla_sync_status`; staged digest exactly the staged value; readback digest
exactly the transient value; and the aggregate/per-field mismatch shape consistent
with that one change. **Anything else is an immediate permanent indeterminate
mismatch** with the schema-2 bounded diagnostic.

**The wait is read-only and bounded.** Lock-before-write and the single fixed
`PUT` are unchanged. After the ordinary immediate readback:

1. already exact -> succeed with `convergence_used: false`. No sleep, no extra GET.
2. exactly the transient -> follow the fixed schedule; **one fresh GET per step**;
   re-verify the exact class and re-run the aggregate, all 37 per-field hashes and
   the complete metadata projection every time.
3. complete staged state returns -> succeed with `convergence_used: true`, the
   attempt count, elapsed seconds and `final_state: exact_staged_protected_state`.
4. still the same transient -> keep waiting, within the bound.
5. anything else moves -> lock `indeterminate` immediately.
6. schedule expires -> lock `indeterminate`, reason class `ConvergenceTimeout`,
   `final_state: pending_not_accepted`.

**The transient is never success.** A commit succeeds only when the complete
protected state is exactly the staged state again. There is no second `PUT`, no
retry, no rollback, and nothing is ever written to Google metadata. On a
multi-target plan the next target starts only after the previous one is exact
again; a timeout or a mismatch stops the loop permanently.

**What the records may say.** On success: `convergence_used`, the attempt count,
bounded elapsed seconds, the fixed meta key and the final requirement/state. On
timeout: the fixed plan, action and endpoint, the reason class, attempt count,
elapsed seconds, the meta key and the two fixed hash identifiers, and
`final_state: pending_not_accepted`. Never a raw page, product or metadata value,
header, body, credential or traceback. Unexpected GET errors during the wait stay
sanitized under the existing schema-2 rules.

**Known bound.** The 90-second ceiling is **per target**. A plan of many eligible
targets can therefore spend up to 90 seconds each, so stage small - one target, or
a short enumerated batch - and let the job runner own anything longer.

### Checkout guard - NOT deployable from here

WooCommerce's REST API can manage shipping classes and assign them, but it exposes
no route that installs site-side checkout logic. This connector deliberately has
no plugin-upload, file-write or PHP-execution capability, and none was added.
`woocommerce_shipping_policy_tool.py deploy-checkout-guard` therefore exists only
to refuse, and always does.

The guard ships as a hand-installed WordPress plugin under
`freight_checkout_guard/`. Build the ZIP with `python build_plugin_zip.py`; the
archive is byte-reproducible so its SHA-256 can be checked against the build
report. Install it through **Plugins > Add New > Upload Plugin** on staging first.

The archive contains four members: the plugin PHP, `readme.txt`,
`ups-allowlist.json`, and `assets/frpdepot-freight-notice.js` (added in 1.0.1).
Rebuild it after any source change - the checked-in ZIP is not regenerated
automatically, and installing a stale one reinstalls old behaviour.

The guard is **default-deny**. An item is UPS-eligible only when a current,
unexpired `ups-allowlist.json` names it by exact product ID, exact variation ID
and exact non-empty SKU, and the item carries no shipping class. Everything else
requires a freight quote: the freight class, a blank or unknown class, a variation
inheriting its parent's class, a missing or mismatched SKU, an unresolvable line,
or an allowlist that is missing, unreadable, malformed or expired. One such item
blocks the whole cart, so mixed carts are blocked.

The shipped allowlist is **empty** (`verified_packing_groups: 0`), matching the
manifest: all 136 classified variations are freight-required until their packing
groups are measured and independently verified. Adding an entry is a separate
approved change, not a side effect of this tool.

Blocking happens on `woocommerce_package_rates` (all rates removed), the two
no-shipping-HTML filters (exact message), `woocommerce_check_cart_items`,
`woocommerce_checkout_process`, `woocommerce_after_checkout_validation`,
`woocommerce_store_api_cart_errors` and
`woocommerce_store_api_checkout_update_order_from_request`. Cart and checkout
validation iterate the full cart rather than the shipping package, so a virtual
item cannot bypass the guard by escaping rate calculation.

The customer message is exactly, and only:

    Contact us for a freight quote.

### 1.0.0 failed production validation on 2026-08-09 - do not reactivate it

Rachad installed and activated 1.0.0 on production. It blocked checkout, but the
customer never saw the message: the page showed only WooCommerce's own generic
cart-errors wording. He deactivated it the same day. Version 1.0.0 remains
installed but inactive, its plan is permanently failed, and its ZIP
(`4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25`) must never
be installed again.

**Cause.** Adding an error notice on `woocommerce_check_cart_items` is what blocks
the checkout - and it is also what destroyed the message. When cart-item errors
exist at page-render time WooCommerce does not render the checkout form at all: it
renders `checkout/cart-errors.php`, whose entire visible content is that generic
sentence, and then calls `wc_clear_notices()`. The exact wording was discarded
before any code printed it. The Cart/Checkout Blocks path inherited the same
failure - the block is not mounted in that branch, so the browser never calls the
Store API, so `woocommerce_store_api_cart_errors` never ran either. Blocked was
right; the shell and the missing sentence were wrong.

**Fix, in 1.0.1.** Emit the sentence into the render path that swallows it rather
than relying on the notice queue surviving:

- `woocommerce_before_template_part`, narrowed to `checkout/cart-errors.php`,
  prints the message immediately above the generic shell. It anchors on
  `wc_get_template()` itself, so it does not depend on the contents of that
  template and it fires for both the classic renderer and the block renderer's
  server-side fallback.
- `woocommerce_cart_has_errors` is a second anchor inside the same shell.
- `assets/frpdepot-freight-notice.js` is a bundled static script - one fixed local
  file, no dependencies, no inline script, no localised data - enqueued only on a
  cart/checkout page whose cart is already blocked. It inserts the sentence into
  the Blocks UI only if it is not already on the page, and withdraws its own copy
  if WooCommerce renders that sentence itself.

Every emission point runs through one latch, so **at most one** copy of the
sentence is emitted per request, however many surfaces fire. The blocking notice
is deduplicated for the same reason. Blocking, rate suppression and the
default-deny decision core are unchanged; the shipped allowlist is still empty.

**Runtime status.** The behavioural proof of the fix lives in
`freight_checkout_guard/tests/test-freight-guard.php` and needs a PHP runtime.
PHP is not installed on this server, so that harness is **pending**: it has never
been executed. The Python suite proves the structural invariants only. Treat the
message as verified only after the PHP harness passes and an anonymous browser
sees the sentence on staging.

## WordPress plugin deployment (UI, approval-gated)

Commissioned by Rachad Homsi on 2026-08-09 so he can approve WordPress work from
his phone. Commissioning authorises building and testing. It is **not** approval
of any site change.

`wordpress_plugin_deployment_tool.py` is the only route in this tree that can
install or activate site-side code, and it can do so for exactly **one** plugin.

### Scope

| | |
|---|---|
| Plugin | `FRP Depot Freight Checkout Guard` |
| Plugin file | `frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php` |
| Site | `https://frpdepots.com` |
| Artifact | `freight_checkout_guard/frpdepot-freight-checkout-guard.zip` |
| Version | `1.0.1` |
| SHA-256 | `fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb` |

All six values are hard-coded constants. The caller supplies no URL, path, slug,
ZIP, PHP, selector or free-form action, so no other plugin, file or page is
reachable. Version `1.0.0` / `4d8396d9...` is refused everywhere in the tool: it
can never be staged, approved or installed again.

There is **no** plugin deletion, no arbitrary install, no REST call, no shell, no
settings/theme/user/post/media/comment/order/customer/payment/refund write, and
no generic browser action. There is no `fill`/`type` call anywhere in the module,
so no customer, address or payment data can be entered, and no order can be
placed.

### Browser access

Admin work attaches to the **already authenticated** loopback CDP session at
`127.0.0.1:9229` that Rachad opens with `CONNECT_DADO_WORDPRESS_UI.bat`. The tool
never launches a browser profile and never signs in; if that window is closed it
refuses rather than recovering. Navigation is restricted to three admin paths -
`plugins.php`, `plugin-install.php`, `update.php` - and every click is preceded by
an origin check.

The post-activation validation is deliberately the opposite: a throwaway headless
Edge with no persistent profile and no stored state, so it carries no admin cookie
and sees exactly what an anonymous customer sees.

Only a privacy projection ever leaves a page: `present`, `active`, `version`,
`update_marker`, `plugin_file` and a fingerprint over those five. No HTML, no page
text, no screenshots, no other plugin's row, and no error message carrying page
content.

**Row scoping.** Everything anchors to
`tr[data-plugin="…"]:not(.plugin-update-tr)`. The `:not()` is load-bearing: when an
update is pending WordPress emits a *second* row with the same `data-plugin`, and
without the exclusion the fixed row would look ambiguous and every action would
refuse. State is read twice - from the row's class and from which row action is
offered - and the two must agree, otherwise the screen is treated as ambiguous and
nothing is clicked.

### Commands

    inspect                                          # read-only
    preflight-validation                             # read-only; 3 fresh anonymous rehearsals
    stage-replace       / commit-replace    --plan <p> --approval APPROVED
    stage-activate --preflight <e>
                        / commit-activate   --plan <p> --approval APPROVED
    stage-deactivate    / commit-deactivate --plan <p> --approval APPROVED

Staging is read-only: it inspects the live row, verifies the local artifact, and
writes a hashed plan into `Dado\20_Working\wordpress_plugin_plans\`. It reports
`external_write_performed: false`.

`stage-replace` requires the installed target to be exactly `1.0.0` **inactive**;
an already-current `1.0.1` is refused as a no-op and an active plugin is refused
outright. `stage-activate` requires exactly `1.0.1` inactive, refuses `1.0.0`
by name, and refuses outright without fresh passing preflight evidence.

Commit re-verifies the plan hash, closed schema, expiry, origin, tool, action,
plugin identity, artifact hash/version/members and live pre-state, then requires
Rachad's own one-word `APPROVED`. The approval is checked **before any browser is
opened**. A replay lock is created atomically **before the first side effect**; the
read-only pre-state check runs before the lock, so a closed window or a moved row
refuses without burning the plan.

`commit-replace` uses **Plugins > Add New > Upload Plugin**. Because the
destination exists, WordPress shows its replace-current comparison screen; the tool
verifies that screen names the fixed plugin and offers version `1.0.1` before
clicking the replacement control, and refuses if the table or control is missing or
ambiguous. It verifies the row reads back `1.0.1` **inactive** and never activates.

### Preflight rehearsal (required before an activation can be staged)

Added 2026-08-09 after activation plan `…4841d651c0e89698` activated `1.0.1`, hit a
**bare `TimeoutError`** in the anonymous validation, rolled back correctly - and
could not say *which* sub-step had timed out. That plan is permanently closed and
1.0.1 is inactive.

`preflight-validation` rehearses the read-only half of that validation **before
anything is activated**, on throwaway anonymous contexts with a *narrower* public
allowlist (`/` and the product page only, so a cart or checkout is unreachable
rather than merely unvisited). Each rehearsal loads the homepage and the product
page, refuses a blank or fatal page, requires exactly one variations form, selects
the three fixed attribute values through the visible customer controls, proves the
variation is **ready** (below), and stops. It clicks no Add to cart, creates no
cart, visits no checkout, opens no admin session and writes nothing.

**Three consecutive fresh-browser passes (tool `1.2.0`).** One invocation runs
exactly **three** rehearsals, each in a brand-new context with no shared cookies or
storage, and **all three must pass**. The run stops at the first failure and
records it; that evidence can never be staged on. Rachad asked for three because a
single pass on a page this dynamic is a sample, not a habit.

It records only booleans, fixed labels, per-step timings and the control method
into `Dado\20_Working\wordpress_plugin_preflight\` - never page body, HTML, a
variation id, a screenshot, storage or any secret - and hashes the record.
`stage-activate` refuses unless the evidence passed all three runs in order, names
this exact tool version / preflight schema / site / product / variation, and is
**at most 30 minutes old**. The 30 minutes are measured from the moment the
**third** pass finished, not the first. The plan then embeds the evidence hash,
timestamp and run count, and `commit-activate` re-checks all of it before touching
a browser. Tampered, substituted, reordered, short, failed or stale evidence
refuses without burning the plan.

**Live control shape (corrected 2026-08-09).** A read-only inspection of the live
FRP Pipe form measured what is actually there: one **hidden backing `<select>`** per
attribute row under visible **`<li role="radio" data-value="…">`** options
(`variable-item button-variable-item`). There is **no `input[type="radio"]`** on
that page. Tool `1.1.0` assumed there was, so every attribute silently took the
hidden-select branch - which changes the value and passes the read-back honestly,
but never runs the theme's click handlers, so WooCommerce never resolved a
variation and Add to cart stayed disabled. That is the exact cause of the
`add_to_cart_disabled` failure of plan `…83f9fa35eec3cb88`.

Selection now queries only `[role="radio"][data-value]` inside the one fixed
attribute row and matches the required value **in Python** by exact `data-value`
(nothing is interpolated into a selector - the fixed values contain a `"` and a
`/`). Exactly one visible, non-disabled match is required. The forced backing
select survives **only** for a row with no role-radio controls at all; if
role-radio controls exist and the exact value is absent, the tool **refuses**
rather than guessing through the hidden select. Either way the backing value must
read back **exactly**. Recorded method is `visible_role_radio` or `backing_select`.

**The `variation_ready` gate.** A read-back proves the *select* agrees with us; it
proves nothing about whether *WooCommerce* considers the variation purchasable.
That is now its own step, run after the three selections and before Add to cart, in
both the preflight and the activation validation. It polls, bounded (20s), until
there is exactly one `input.variation_id` inside the one form holding a **positive**
id (only `variation_resolved: true` is ever recorded, never the id) and exactly one
`button.single_add_to_cart_button` that is present, visible, and carries no
disabled property, attribute or class token (`disabled`,
`wc-variation-selection-needed`). Refusal codes are fixed and page-text-free:
`variation_id_missing`, `variation_id_ambiguous`, `variation_unresolved`,
`add_to_cart_missing`, `add_to_cart_ambiguous`, `add_to_cart_not_visible`,
`add_to_cart_disabled`.

### Activation validation and automatic rollback

`commit-activate` activates, verifies the row is active, then immediately runs the
anonymous public test staged into the plan (so Rachad approves the rollback
criteria at the same time as the activation). Every sub-step has a fixed name:

    home_load  product_load  variation_form
    select_SIZE  select_PRESSURE_RATING  select_RESIN_TYPE
    variation_ready  add_to_cart  checkout_load  checkout_assertions

1. Load `https://frpdepots.com/product/frp-fw-pipe/`.
2. Select SIZE `1/2"`, PRESSURE RATING `150PSI`, RESIN TYPE `D411` through the same
   visible customer controls the preflight used.
3. Prove `variation_ready` with the same helper the preflight used.
4. Add to cart, confirming no fatal.
5. Go to checkout.

Success requires **all** of: the exact text `Contact us for a freight quote.`
appears exactly **once**; checkout is blocked; no checkout form or payment
submission is available; no blank/fatal page; the plugin is still active; the UPS
setting untouched.

Any missing or duplicated message, generic-only message, available payment or
checkout form, PHP fatal, blank page, navigation anomaly, plugin mismatch, or
validation that cannot be judged triggers **immediate emergency deactivation of
only the fixed plugin, exactly once**. The rollback verifies the row reads inactive
and that the homepage and cart recover, records the failure, and closes the plan
permanently. A successful validation never deactivates.

**Failure attribution.** Every failure that reaches rollback records the fixed
step name *plus the exception class* (`{"step": "checkout_load", "exception_class":
"TimeoutError"}`), never a bare exception and never exception text - a refusal code
survives only if it is one of the tool's fixed vocabulary, so no page content can
reach a receipt. Timeouts are explicit and bounded (45s navigation, 15s action, 8s
read-back, 20s readiness) and a timed-out required step **fails closed**: a
partially rendered page is never a pass.

Playwright sessions are never nested: the rollback runs after the admin session
closes.

### Plans

Closed schema, full SHA-256, nonce, 24-hour expiry, path-confined to the plan
folder, one-use. A failed, committed, expired, tampered or locked plan can never be
replayed or retried. Every commit writes a result sidecar (`*.result.json`), a lock
(`*.commit-lock.json`) and a receipt. An indeterminate result leaves the lock in
place and refuses retry - reconcile in WordPress and stage a new plan.

Approval for one plan authorises no other. The tool never produces the word
`APPROVED` on Rachad's behalf.

### Tests

    python -m unittest discover -s Dado/Tools/woocommerce -t Dado/Tools/woocommerce -v
    php Dado/Tools/woocommerce/freight_checkout_guard/tests/test-freight-guard.php

The checkout-guard scenarios live in
`freight_checkout_guard/freight_guard_scenarios.json` and are executed by both
harnesses. The Python suite runs the real PHP when `php` is on PATH; when it is
not, it runs a Python reference model of the same decision core, says so on
stderr, skips the PHP test rather than reporting a pass, and separately asserts
that the two implementations use an identical reason vocabulary.

`test_wordpress_plugin_deployment.py` is fully offline: no Playwright, no CDP, no
browser, no network. The Plugins screen, the upload/comparison screens and the
storefront are modelled by a fake DOM with a small real CSS matcher, so the tool's
own selectors, scoping, ordering and refusals are genuinely exercised rather than
mocked away at the boundary. `TestFakeEngineIsHonest` pins the matcher down - a
permissive fake would silently invalidate every scoping test in the file. The
fakes model an unrelated plugin row and a live Delete link and fail the run if
either is touched, and they record whether the replay lock existed at the moment
of the upload and of each click.

The storefront fake models the **measured** live control shape - visible
`<li role="radio" data-value="…">` options over one hidden backing select per row,
and no `input[type="radio"]` anywhere. It **refuses an unforced selection on a
hidden select** and an unforced click on a hidden element exactly as a real page
would, refuses any navigation/click/selection/text read that arrives without an
explicit bounded timeout, and can inject a one-shot Playwright-shaped
`TimeoutError` at any of the ten named steps - so the fix for the bare-timeout
incident is tested at every step it could have happened at.

Crucially the fake **derives** the variation id and the Add to cart button state
from the selections rather than taking them from a flag. Forcing the hidden select
therefore changes values and still leaves the button disabled, exactly as
production did - so `visible role-radio preferred`, `no guessing fallback` and
`variation_ready` are properties the suite can actually falsify. A fresh anonymous
context starts with nothing chosen, which is what makes "three independent
rehearsals" checkable rather than a count of loop turns.

## Fixed orphan-media cleanup commission (2026-08-20)

Rachad commissioned `wordpress_orphan_media_cleanup_tool.py` for one fixed cleanup
only: permanently delete WordPress attachment IDs **5521, 5523, 5525 and 5527**.
They are the four exact, hash-recorded open-manway uploads left unattached when
operation `877ff133...` permanently locked before any product-gallery payload.
The tool requires two complete, matching read-only evidence passes before staging:
the full Media Library, the server guard proving exactly those four
`unreadable_original` failures, exact attachment edit identity/status/unattached
metadata and exact delete controls, every registered original/thumbnail URL and
current public-byte state, and strict total-reconciled WooCommerce product plus
variation walks proving zero references. The source result file, source operation,
fixed IDs, filenames, URLs, byte sizes and SHA-256 values are pinned in code.

Only `stage` and `commit --plan --approval` exist. Staging writes no website data.
Commit requires Rachad's later exact unpadded uppercase `APPROVED`, takes the shared
WordPress browser lock before its permanent attempt lock, and can click only the
four fixed `Delete permanently` controls in that fixed order. Every target writes
its own immutable attempted/verified journal and receives complete Media Library,
server-guard, product/variation, authenticated-record and public-file verification
before the next click. WordPress may remove every registered derivative listed in
the plan. The four deletes are
**not atomic and have no rollback**: earlier deletions remain if a later action or
verification fails, later deletions are not attempted, and the plan becomes
`INDETERMINATE_NO_RETRY`. There is no retry, restore, upload, edit, rename,
replacement, attachment/product/plugin/setting/order/customer/payment or mail
route, and no generic browser command.

## Media Guard 1.0.7 + fixed Open Manway gallery recovery 2.1.0 (2026-08-22)

**STATUS: BUILT AND TESTED ONLY.** Guard **1.0.5 remains the live plugin**. No
deployment plan and no recovery plan exists from this build, nothing was staged,
approved, uploaded, deployed or written, and no email was sent. Recovery 2.1.0
**cannot stage against live 1.0.5**: it pins Guard 1.0.7 and refuses at `stage`,
before creating any plan, when the live guard is not the pinned build. Deploying
1.0.7 needs its own later immutable plan and Rachad's own later go.

**GUARD 1.0.6 IS WITHDRAWN AND WAS NEVER DEPLOYED.** It was built on 2026-08-21
under the same authority, then an independent verification proved it NOT READY.
Its bytes are kept unchanged as rejected evidence
(`media_mutation_guard/frpdepot-media-mutation-guard-1.0.6.zip`, 31,640 bytes,
SHA-256 `6a753c570d167075b8fa0a66349ab0a812aa7e222a7aedb2f6d374b913a7010e`), it is
classified `WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE` in the plugin readme, both
Python tools and their tests, and **both tools refuse it by hash**. No 1.0.6 plan
was ever staged. The old build result
`Dado\20_Working\frp_manway\open_manway_guard_106_recovery_v2_build_result.json`
is left unedited as the record of what was rejected; the correction result is a
new file beside it.

### What the verification found, and what 1.0.7 changes

The 1.0.6 build was internally coherent and its tests passed, but its **producer
and its consumer disagreed**, and its tests could not see it:

1. **Fatal: the proofs the plugin actually emitted could never be accepted.**
   `frpd_mg_snapshot()` and `frpd_mg_completion_proof()` returned `schema => 2`
   while the Python consumer pinned `3`. Two further shapes disagreed that nobody
   had noticed at all: the plugin's own recovery projection published **nine**
   keys against a five-key consumer, and its completion proof published
   `attachment_identities` the consumer's closed schema rejected. The PHP suites
   never called the completion path for Open Manway, and the Python tests
   fabricated proofs instead of consuming real ones, so nothing failed.
   **1.0.7 makes this executable.**
   `media_mutation_guard/test_media_mutation_guard_recovery_lifecycle.php` drives
   the REAL plugin through acquisition, guarded progress, five reserved uploads,
   the owner-bound gallery commit, the completion proof and the terminal
   `completed` transition against a library built to the real pinned totals (364
   attachments before, 369 after, one private Hetron exception), and publishes
   every proof it produced to
   `media_mutation_guard/testdata/guard_107_proof_contract.json`. The Python suite
   validates that file with the production validators. A producer/consumer drift
   is now a failing test, not a claim.
   Completion also validates its proof **before** the terminal state write, and a
   `guard_completed` proof is only rendered once every closed schema and terminal
   predicate has passed.
2. **An expired unresolved row could be overwritten - a semantic retry.**
   `frpd_mg_active_state()` collapsed every expired row to `null` before decoding
   it, and acquisition then replaced it with an unconditional
   `ON DUPLICATE KEY UPDATE`. A recovery that expired holding a reserved-but-unbound
   upload could be silently reacquired. 1.0.7 splits the reader: `frpd_mg_exact_state()`
   decodes and validates the row **whether or not it is expired**, and
   `frpd_mg_active_state()` returns it only while it is live. Replacement is a
   compare-and-swap keyed on the exact status, version and both durable JSON blobs
   that were inspected, and only a **strict terminal safe state** may be replaced -
   a completed row, or an `expired` row with no reservation and no binding beyond
   its acquisition bindings. Everything else refuses and is preserved
   byte-semantically. The automatic retire-to-expired write is gone. There is
   deliberately **no** retry, reset, force-unlock, cleanup, deletion or reacquire
   route anywhere: recovering a second incident needs its own commissioned path.
3. **Attachment identity was incomplete.** 1.0.6 proved post type, non-trash
   status, basename, bytes and hash. 1.0.7 proves one closed identity - exact
   positive ID and fixed position, post type `attachment`, post status exactly
   `inherit`, MIME exactly `image/png`, exactly one byte-exact `_wp_attached_file`
   value, a byte-canonical contained regular non-link original, exact basename,
   bytes and SHA-256, and the PNG signature with IHDR width, height, bit depth,
   colour type and mode - for **every** fixed attachment, including 7609. The
   complete snapshot records those identities and folds them into its digest, and
   the completion proof re-validates all six.
4. **The origin-only proof did not prove what it claimed.** 1.0.6 aggregated every
   discovered path's owners into one de-duplicated list and then compared counts,
   so "one unowned file plus one two-owner file" reported clean. Each discovered
   relative path now carries its **own** exact owner ID list and is classified
   alone; zero owners, more than one owner, more than one discovered copy of a
   fixed basename, a wrong owner identity or a collation-ambiguous match all
   refuse. Ownership is queried with an explicit `BINARY` comparison **plus** a
   second probe that must find no collation-equal-but-byte-different row, so the
   live case-insensitive `postmeta` collation cannot produce a false match.
5. **The uploads root itself could be an alias.** Year and month directories were
   checked; the root was simply `realpath()`ed. 1.0.7 requires the configured root
   to equal its own canonical path and to be a readable non-link directory, using
   the same predicate as every year and month directory.
6. **The admin-post surface contradicted its own contract.** The documented
   three-field form required a fourth caller-supplied `_wp_http_referer`. The form
   no longer renders one, the handler refuses a body carrying one, and the raw
   request body is parsed and compared to `$_POST` so a duplicate or reordered wire
   field cannot hide. Query parameters, file fields, a non-form content type and a
   `GET` are all refused.
7. **A post-claim failure claimed the state was unchanged.** The REST filter
   claims `active -> gallery` before WooCommerce runs the update, so a downstream
   failure can leave the row in `gallery`. The failure proof now re-reads the state
   and says so outright, records the observed status, version and dispatch status,
   and never mentions rollback.

### Guard 1.0.7 artifact

`media_mutation_guard/frpdepot-media-mutation-guard-1.0.7.zip`, reproducible,
built twice to independent paths and byte-identical. The immutable 1.0.5 source
snapshot stays at `media_mutation_guard/released/1.0.5/`, byte-identical to the
pinned 1.0.5 ZIP, and `wordpress_product_family_media_tool.py` still pins **that** -
it drives the version that is actually live. The exact 1.0.7 byte counts and
SHA-256 values are recorded in
`Dado\20_Working\frp_manway\open_manway_guard_107_recovery_210_correction_result.json`
and pinned in both Python tools.

### Recovery tool 2.1.0 - the two production defects that mattered

`wordpress_open_manway_gallery_recovery_tool.py` is tool version **2.1.0**, schema
**3**; every 2.0.0 plan is permanently superseded by identity. Only `stage` and
`commit --plan --approval` exist.

- **It became permanently stuck after the first successful upload.** The predicate
  compared the *evolving* live reconciliation to the *frozen* staged one, so the
  moment upload 2 landed, upload 3 - and eventually the gallery commit and
  completion - refused. Because that happens **after** the attempt lock, an
  otherwise successful recovery would have ended `INDETERMINATE_NO_RETRY`. The
  staged reconciliation is now the immutable **acquisition baseline**, and one
  normalized progression model derived from the guard's **own durable record**
  (immutable missing positions, immutable acquisition bindings, current
  reservations, bound uploads, remaining positions, one optional unbound
  reservation) says what live state must look like after N uploads. That model is
  used at stage, at the fresh commit preflight and immediately before every side
  effect. An attachment appearing at a future missing position that the guard
  cannot account for, a disappeared binding, a substituted or duplicated ID, or a
  journal that disagrees with the durable record are all external drift and refuse
  **before** the next side effect. One reserved-but-unbound upload stops
  everything. The gallery commit requires all six bindings and no unresolved
  reservation.
- **The product legitimately changes exactly once.** Before the gallery commit the
  fresh product read must equal the staged baseline outright; after it, the gallery
  IS the recovered six and WordPress has moved `date_modified_gmt`, while every
  other field - the whole protected projection and its fingerprint - must still be
  byte-identical. Comparing the post-commit product to the pre-commit baseline
  outright is what made `complete_guard`, which runs after the write, refuse.

**Two transports, named separately and truthfully.** The commission forbids
Basic/generic WooCommerce for the **gallery write**, not for the commissioned
read-only verification, and 2.0.0's plan claimed both at once: it pinned the
authorization as `PUT /products/1397` and listed "no Basic credentials / no generic
REST" as forbidden, while the module loaded the Woo vault and read the product with
`wc.api_get()`. The plan now carries `read_transport` (read-only
`woocommerce_common.api_get` GETs of `/products/1397`, for exact product identity,
gallery order and the protected-field fingerprint) and `write_transport` (the
owner-bound `admin-post` route, exactly three caller-supplied fields, product and
attachment IDs derived server-side) as separate fields, and
`assert_no_woocommerce_write_primitive()` **parses this module's own source** and
refuses if any `api_request`/POST/PUT/PATCH/DELETE call site exists at all. A test
plants a real write call into a copy of the source and proves the check fails on it.

The `commit_recovery_gallery` browser adapter also gets its own form validator:
this is the one guard form with **no** `_wp_http_referer`, and the shared 1.0.5
validator would have rejected the one correct form.

### Recovery suite 2.1.0

Refreshed end to end: 139 tests, zero stale symbols, zero failures, zero errors,
zero skips. The fake admin is now a small live world - reserving and binding an
upload changes what every later read reports - so a frozen fake can no longer hide
a progression defect. `wc.api_request` is mocked to **raise** if it is ever called.
Ordering is asserted on runtime event sequences, not source-substring positions.
`read_guarded_live_state` and every post-upload transition are exercised, including
0-through-5 missing subsets and position 2 already live. The temporary receipt log
is seeded with the **exact permanent prior line** rather than being emptied, and
every prior artifact - plan, result, attempt, all four event-journal files and the
receipt - is proven byte-identical across a stage, a successful mocked commit and
each of nine failure branches.

### Deployment tool 1.7.0 - the one new transition

`wordpress_media_guard_deployment_tool.py` is tool version **1.7.0**, schema
**11**, and permits exactly one new transition: replace an **exact installed,
active, healthy, unchanged 1.0.5** with **exact 1.0.7**. The withdrawn 1.0.6
artifact is refused by hash and by its embedded version string.

One normalized `assert_deployment_eligibility()` governs stage, the fresh commit
preflight, **and** `AdminPage.execute_replace()` immediately before the first
upload form submission - 1.6.0 validated only at stage, re-implemented a subset
inline at commit, and chose and submitted the artifact with nothing fresher than
the preflight. Anything that drifts in between is now a **free** refusal with no
file uploaded.

Live pre-state health proves more than version text: the exact plugin row, active,
no update marker, `Guard inactive`, the five fixed family sections, no capability
projection (that absence is 1.0.5's expected fixed shape), no guarded-snapshot,
completion or recovery-commit controls (their absence is the proof that no guard or
recovery state is unresolved), and no JavaScript error. Each of the **three**
post-replacement rounds proves the exact plugin row **and** the exact health
together - version 1.0.7, active, no update marker, the exact capability with both
schemas and the runtime-manifest digest, no unexpected state, no JavaScript error.
1.6.0 verified health three times and read the row once afterwards, so no single
round proved active-and-healthy at the same moment.

One attempt, no retry, rollback, delete or cleanup. The existing emergency
deactivation route is unchanged and was not widened. There is no arbitrary plugin,
setting, content, media, product, user, order, customer, payment, mail or generic
browser route.

### The permanent prior incident is unchanged and unchangeable

Plan `1c7865b0287b076fe83c179c2e44f33a3bcb2effb048e87374c32ad4781b19df`,
operation `e0127fcaa04c023cbdd19a36726d6e8f03c3fb01f12f0367550d17c87674dc85`,
result `INDETERMINATE_NO_RETRY` at `upload_2`. Attachment 7609 is verified at
position 1; position 2 may have landed; positions 3-6 were never attempted; the
product gallery PUT never happened. The plan, result, attempt, all four
event-journal files and the exact receipt line are pinned by size and SHA-256 and
are never opened for writing.


## Revoke access

In WordPress, open **WooCommerce > Settings > Advanced > REST API**, locate the Dado
key, and select **Revoke**. Then remove the local DPAPI vault file. Do not send the
old values to Dado.
