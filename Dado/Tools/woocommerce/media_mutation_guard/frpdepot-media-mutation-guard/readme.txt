=== FRP Depot Media Mutation Guard ===
Contributors: frpdepot
Requires at least: 6.4
Requires PHP: 8.0
Stable tag: 1.0.2
License: Proprietary

Fixed internal safety plugin for FRP Depot's approved five-family product-gallery workflow,
including the staged 2026 catalogue-image update for the filament-wound pipe family.

It serializes in-scope WordPress attachment mutations with one hard-coded MySQL advisory
lock. While that lock is held, it builds complete original-file SHA-256 snapshots and
reads/writes one versioned guard row directly in a fixed InnoDB table, bypassing the
WordPress option and object caches. Database UTC controls the fixed 30-minute expiry.

One fixed exception exists for intentionally private Hetron attachment 1832. Readable or
unreadable, a snapshot accepts it only when its complete attachment identity remains exact
and the active Hetron protector plugin's live PHP source matches its pinned SHA-256. The
protected attachment is represented in the snapshot digest; every other non-trash
attachment must still expose a readable original file and fresh hash.

One owner-bound guard permits only the selected family's four byte-pinned PNG uploads.
After all four are captured, one images-only WooCommerce REST `PUT` must carry the exact
four captured IDs as its sole JSON parameter, no query/form/file parameters, and a
non-secret `If-Match` SHA-256 of the complete live pre-write gallery ID list. Under the same
advisory lock, the guard verifies that precondition and atomically moves its row from
`active` to `gallery` before permitting only those exact featured/gallery metadata values.
Ordinary and metadata-by-ID post-meta APIs resolve aliases with the live `meta_key` column's
exact MySQL character set and collation, failing closed if that schema proof is unavailable,
so competing gallery writes remain blocked until completion or expiry. WordPress core's
alternate-image filename collision scan remains enabled and is never overridden by this plugin.
Completion is not a cancel or cleanup route: it succeeds only after a fresh complete
Media Library rehash proves exactly the four captured attachment IDs and the fixed
WooCommerce product proves the same featured/gallery IDs in manifest order. The guard
row is retained with terminal status `completed`; an expired `active` or `gallery` row is
retained as `expired`. Neither path deletes attachments or product data.

The locking contract requires one authoritative MySQL server. It does not claim safety
through a read/write split, proxy connection multiplexing, pooled-connection behavior
that changes lock ownership, or independent primaries. There is no force-unlock or
age-based advisory-lock reclamation; stale recovery requires explicit DBA termination
of the owning database connection.

This is an ordinary WordPress plugin. Its enforcement boundary excludes direct database
or filesystem mutation, malicious PHP running in the same process, and a privileged
administrator who disables or replaces the plugin. Extending protection to those actors
would require an MU-plugin or host-level control.

There is no public endpoint, REST write route, generic filename/hash/product input,
attachment delete route, product/order/customer/payment operation, or email transport.
Installation and activation are separately approval-gated by the commissioned fixed
deployment tool.
