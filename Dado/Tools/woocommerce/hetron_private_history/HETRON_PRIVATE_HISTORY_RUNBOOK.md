# Hetron Private-History Protection — Fixed Repair Runbook

**Scope:** WordPress attachment `1832`, slug `hetron-cr-guide-2007_ineos`, and only its exact PDF plus four fixed JPEG previews.

## Reconciled production state — 2026-08-13

- Attachment `1832` is `private`; its two anonymous WordPress routes return `404`/`410` without redirect.
- Plugin v1.0.0 is installed and active.
- Its activation plan is permanently `INDETERMINATE/no-retry`; never retry or remove that lock.
- The v1.0.0 destination one directory above the document root was same-filesystem but not writable.
- No file moved. All five upload files remain public and byte-exact.
- The direct PDF remains reachable from nginx and previously carried a one-year public cache header.

## Why v1.1.0 uses a prepared hidden directory

Nginx ignores `.htaccess`, so an Apache/LiteSpeed `.htaccess` plan is rejected for this host. A hidden name alone is not treated as protection.

The corrected plugin pins one uploads-root directory:

`wp-content/uploads/.frpdepot-private-history-hetron-1832`

The protection sequence is split into three separately reviewed plans:

1. replace active v1.0.0 with exact active v1.1.0;
2. create only that fixed directory at requested mode `0700` and one exact 45-byte canary at requested mode `0600`;
3. stage protection only if anonymous nginx requests to both the literal canary URL and a fixed cache-busting query variant prove `403`, `404`, or `410` without redirect or canary bytes.

If nginx can read the canary, protection cannot be staged and all five historical files remain untouched. If denial is proven, protection moves only the five fixed byte-pinned files into the already prepared directory. Post-write verification checks both literal and cache-busting URLs for every old public path and every hidden destination path.

This proves current site behavior; it cannot revoke copies already downloaded or retained in a visitor's browser cache.

## Fixed plugin capability

The plugin accepts no attachment ID, path, URL, filename, role, hash, or destination from a request. It contains only:

- one authenticated read-only probe;
- one nonce-protected fixed preparation action;
- one nonce-protected fixed five-file protection action;
- five fixed authenticated historical-download actions.

There is no restore, delete, deactivate, uninstall, arbitrary path, rewrite, `.htaccess`, email, REST write, or settings action. Recovery or public restoration requires a separately commissioned immutable tool and plan.

Each historical download requires a logged-in user who can upload files and read private attachment `1832`, then rechecks the exact size and SHA-256 before streaming.

## Strict live order

Every plan is immutable, expires after exactly 24 hours, and requires Rachad's later byte-exact unpadded `APPROVED`. Approval is plan-specific and cannot be reused.

### 1. Replace active v1.0.0

```text
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py stage-replace
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py commit-replace --plan <exact-plan> --approval APPROVED
```

Staging requires exact active v1.0.0, private attachment identity, unavailable attachment routes, and all five public source files byte-exact. Commit holds the shared WordPress mutex, rechecks drift, locks before the first upload submission, verifies WordPress's exact same-slug replacement comparison, then reads back exact v1.1.0 still active. It moves no historical file.

### 2. Prepare and test the private root

Only after replacement is verified:

```text
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py stage-prepare
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py commit-prepare --plan <exact-plan> --approval APPROVED
```

The one preparation POST creates the fixed directory and canary. This is a production filesystem write but moves no historical file. Success requires the exact prepared probe, unchanged public source hashes, and nginx denial of both literal and cache-busting canary URLs.

If nginx exposes the canary, the plan closes `INDETERMINATE/no-retry`; stop. Do not move files.

### 3. Protect the five assets

Only after preparation is verified:

```text
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py stage-protect
python Dado/Tools/woocommerce/wordpress_hetron_private_history_deployment_tool.py commit-protect --plan <exact-plan> --approval APPROVED
```

Staging repeats the exact canary-denial proof and full public-source hashes. Commit performs one protection POST. The five renames are not atomic as a set; each rename is one same-filesystem atomic operation.

Success requires:

- plugin v1.1.0 remains active;
- private probe state is exact;
- all five authenticated downloads reproduce exact MIME, byte count, and SHA-256;
- both attachment routes remain unavailable;
- all five literal old upload URLs and five fixed cache-busting variants return `404` or `410`, no redirect;
- literal and cache-busting canary plus all ten hidden asset variants return `403`, `404`, or `410`, no redirect;
- no file was deleted.

## Mutex, attempt locks, and failure behavior

- The shared WordPress mutex is acquired before any plan attempt lock.
- Approval, artifact, expiry, plan identity, and fresh complete before-state are checked before the attempt lock.
- A busy shared browser is a free refusal; no attempt is burned.
- The attempt lock is created before the first side effect.
- Each plan gets one attempt only.
- Any post-lock exception, timeout, partial move, cache ambiguity, or failed readback permanently records `INDETERMINATE/no-retry`.
- Never retry, remove a lock, overwrite an immutable result, automatically roll back, or clean up an abnormal partial state.
- Plans/results live under `Dado/20_Working/wordpress_hetron_private_history_plans/`; durable receipts append to `Dado/40_Logs/receipts.jsonl`.

## Fixed artifact

- ZIP: `Dado/Tools/woocommerce/hetron_private_history/frpdepot-hetron-private-history-1.1.0.zip`
- ZIP bytes: `5319`
- ZIP SHA-256: `bb9012fc9daffeef43d5d551445f72f745ef36b95c72c7a9c428b958c98dd55d`
- Plugin member: 19,011 bytes, SHA-256 `8c06b73a3a76ac2da7e7e9bba25c3f8a31ecdad917b30d436e41b32784b17116`
- README member: 1,824 bytes, SHA-256 `ec13b87367a12747abdc1714812acd16679498e24ab1ac14a83fefaaefd94b00`
- Manifest: `Dado/Tools/woocommerce/hetron_private_history/frpdepot-hetron-private-history-1.1.0.manifest.json`
- Builder: `Dado/Tools/woocommerce/hetron_private_history/build_plugin_zip.py`

The builder is deterministic. Any source or README change invalidates these identities and the deployment tool until deliberately rebuilt, reviewed, and repinned.
