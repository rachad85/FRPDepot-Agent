=== FRP Depot Media Mutation Guard ===
Contributors: frpdepot
Requires at least: 6.0
Requires PHP: 7.4
Stable tag: 1.0.7
License: Proprietary

== Release status ==

Version 1.0.7 is BUILT AND TESTED ONLY. It is an offline Phase-A artifact and is not authorization to stage, activate, or deploy it. Version 1.0.5 remains the released snapshot. The flawed 1.0.6 package is immutable and classified WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE; it must never be installed.

== Purpose ==

This fail-closed WordPress/WooCommerce guard allows one administrator-owned, time-bounded media operation for one of five fixed product families while refusing unrelated media mutation. The fixed families are:

* elbow_90 — product 1401
* manway_cover — product 1399
* open_manway — product 1397
* pipe — product 1364
* stub_flange — product 1368

The plugin embeds no general mutation API. It accepts only fixed manifest identities and durable state transitions guarded by the MySQL advisory lock `frpd_media_mutation_guard_lock`.

== 1.0.7 proof and state contract ==

The runtime manifest schema, durable state schema, and every guard-generated JSON proof use schema 3. The capability projection reports both `state_schema` and `proof_schema`. A completion proof is emitted only after its closed schema and all terminal predicates succeed.

Expired or malformed durable evidence is preserved. Acquisition never retires, clears, overwrites, or retries an expired unresolved `active` or `gallery` Open Manway row. The insert path has no duplicate-key update. Replacement is allowed only for a fully completed row or a clean terminal `expired` row with no reservation or attachment progress.

== Fixed attachment identity ==

Every fixed Open Manway attachment identity is proved using all of these fields:

* exact attachment ID and fixed position;
* post type `attachment`, post status `inherit`, and MIME type `image/png`;
* exactly one `_wp_attached_file` metadata value equal to one safe relative path;
* one byte-canonical regular file below one byte-canonical uploads root, with no root/file alias;
* exact basename, byte count, and SHA-256;
* PNG signature and IHDR width, height, bit depth, color type, and RGB/RGBA mode;
* exactly one live attachment owner for that relative path.

The complete snapshot records the closed fixed identity objects and includes them in its snapshot digest. Completion revalidates all six fixed identities, product thumbnail, ordered gallery, and the exact final attachment total before the state may become `completed`.

== Open Manway recovery ==

This build adds exactly one literal Open Manway recovery contract and nothing generic. The one literal `open_manway_recovery` contract is bound to product 1397 and the prior operation hash recorded in the plugin. Position 1 is the exact previously verified attachment 7609. Positions 2-6 may be either already-live exact attachments or fixed uploads reserved from durable missing positions.

The origin-only file proof scans only the byte-canonical uploads root and direct `YYYY/MM` directories. Root, year, month, nested/file aliases, unreadable directories, escaped paths, scan-limit overflow, and unprovable enumeration fail closed. Each discovered fixed relative path carries its own exact byte/hash classification and its own exact attachment-owner ID list before any file-level classification. Duplicate copies, zero/multiple owners, wrong owners, wrong bytes, or wrong identities refuse acquisition. An origin-only file -- a fixed basename on disk with no exact owning attachment -- is a blocker this plugin reports and never deletes or adopts.

The recovery gallery is committed through one owner-bound WordPress `admin-post` route. Its form has exactly three fields: `action`, `_wpnonce`, and `if_match`. The nonce action is bound to the current durable acquisition owner record, so a URL/form from an older acquisition fails after a newer acquisition. Query parameters, file fields, multipart bodies, nested/scalar mismatches, extra fields, missing fields, raw-body mismatches, and duplicate raw fields are refused.

The route performs at most one internal authenticated REST request. A pre-claim failure leaves the freshly observed state intact. A failure or exception after the `active` to `gallery` claim honestly reports that state remains or may remain `gallery`; it never claims rollback, never resets the row, and never recommends an automatic retry.

== Guard sequence ==

1. Verify the installed version, manifest digest, capability projection, database table, current user/session/cookie ownership, and advisory lock.
2. Generate a complete atomic snapshot and, for Open Manway, the complete origin proof.
3. Acquire one guard row. Any unresolved, malformed, or unsafe prior row refuses acquisition without mutation.
4. Reuse only exact live bindings and upload only the next immutable missing position from the durable record.
5. Commit the fixed Open Manway gallery through the exact three-field owner-bound form, once.
6. Run completion. The state becomes terminal only after all fixed attachment identities, gallery metadata, snapshot counts, and proof fields are exact.

== Fail-closed scope ==

While a valid guard is active, the plugin blocks unrelated attachment insertion/deletion, attached-file changes, metadata writes (including metadata-by-ID paths), image-editor and alternate upload paths, unsupported REST/product mutation, gallery drift, and ownership/session drift. Malformed or unreadable durable state is an error rather than an inactive state.

There is no cleanup, rollback, retry, generic recovery, arbitrary product selector, arbitrary attachment selector, filename/path/URL input, GET mutation route, secret-bearing proof, or deployment action in this package.

== Offline verification ==

The source package is built by `build_plugin_zip.py` with fixed member order, DOS timestamp 1980-01-01 00:00:00, fixed Unix mode metadata, empty ZIP/member comments and extras, and Deflate level 9. The build validates source image identities, manifest closure, PHP pins, version markers, and readme status before writing the ZIP. Build the same output twice to independent paths and compare bytes and SHA-256.

Phase A tests are standalone PHP harnesses. They cover the Guard core, Open Manway acquisition/progress/origin/identity/completion contracts, and the exact admin-post commit success and failure terminals. These tests are offline simulations and do not contact WordPress, WooCommerce, a vault, stage, or production.

== Changelog ==

= 1.0.7 =
* Unified manifest, durable state, capability, gallery, snapshot, origin, and completion proof schemas at 3.
* Preserved expired unresolved and malformed durable evidence; removed automatic retirement/overwrite semantics.
* Closed fixed attachment identity over post/status/MIME/meta/path/PNG/dimensions/mode/bytes/hash/owner.
* Classified origin ownership per discovered relative path and rejected uploads-root/directory/file aliases.
* Reduced recovery commit to the exact three-field acquisition-bound admin-post surface.
* Added fresh observed-state, no-retry failure proofs after one internal dispatch.
* Added real acquisition/progress/gallery/completion and terminal refusal tests plus deterministic build proof.

= 1.0.6 =
* WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE. Retained only as an immutable flawed audit artifact; do not install.

= 1.0.5 =
* Released baseline snapshot; preserved unchanged.
