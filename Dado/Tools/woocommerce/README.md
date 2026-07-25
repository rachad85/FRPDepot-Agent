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

## Revoke access

In WordPress, open **WooCommerce > Settings > Advanced > REST API**, locate the Dado
key, and select **Revoke**. Then remove the local DPAPI vault file. Do not send the
old values to Dado.
