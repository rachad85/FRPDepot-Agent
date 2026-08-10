# Withdrawn 1.0.0 artifact — do not reuse

The failed production artifact was version **1.0.0** with SHA-256:

`4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25`

Rachad installed it on production on 2026-08-09. It blocked checkout but did
not show `Contact us for a freight quote.`; he deactivated it and the linked
plan was permanently closed.

The current `frpdepot-freight-checkout-guard.zip` is a separately built
**1.0.1** artifact with SHA-256:

`fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb`

The complete WooCommerce Python suite passed on 2026-08-09: 118 tests run,
117 passed and one PHP-runtime test skipped because PHP is unavailable on this
host. Two consecutive ZIP builds produced the identical 1.0.1 hash above and
four expected members, including `assets/frpdepot-freight-notice.js`.

The 1.0.1 artifact remains **offline verified only**. A real WordPress/PHP
runtime check is still required. No installation, upload or activation is
authorized by this file; those actions require a new immutable production
plan and Rachad's new one-word `APPROVED`.
