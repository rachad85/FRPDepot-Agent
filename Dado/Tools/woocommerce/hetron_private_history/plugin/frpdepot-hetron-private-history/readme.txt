=== FRP Depot Hetron Private History ===
Contributors: frpdepot
Requires at least: 6.0
Requires PHP: 8.0
Stable tag: 1.1.0

A fixed, non-configurable retirement mechanism for WordPress attachment 1832.

Activation alone does not move or create a historical file. A separate exact
prepare action creates one fixed dot-prefixed directory under the live uploads
filesystem with mode 0700 and writes one harmless fixed access-probe canary.
The deployment tool must prove that nginx denies the canary anonymously before
it may stage protection. If the canary is publicly readable, the five historical
files remain untouched.

The exact authenticated protect action (and only when attachment 1832 is already
private and the canary denial is independently proven) verifies the full byte
count and SHA-256 of one fixed PDF and four fixed JPEG previews, then renames
those five exact files into that fixed prepared directory. Their five old public
upload paths therefore become unavailable without deleting or changing the
historical bytes.

Five fixed authenticated admin-post actions provide historical downloads only
to a signed-in user who can upload files and read private attachment 1832. Every
download is hash-verified immediately before it is streamed.

One exact authenticated restore action is the reverse move after a complete
preflight. Deactivation itself never restores, moves or deletes a historical
file. The deployment tool deliberately cannot call restore; exposing the five
public paths again requires a separately commissioned, approval-gated capability.

Directory creation plus canary creation is not atomic as a set. The five-file
transition is not atomic as a set. Each individual same-filesystem rename is
atomic. Any failure after a side effect is indeterminate and permits no retry or
automatic rollback.
