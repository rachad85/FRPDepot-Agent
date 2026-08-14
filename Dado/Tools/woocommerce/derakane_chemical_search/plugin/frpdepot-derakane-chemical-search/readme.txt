=== Derakane™ Resin Chemical Resistance Guide Search ===
Contributors: frpdepots
Requires at least: 6.4
Requires PHP: 8.0
Stable tag: 2.0.0
License: Proprietary local source; guide content remains subject to source-owner terms.

A read-only, accessible chemical-resistance guide search backed only by a rebuild-pipeline manifest-pinned verified dataset.

== Installation gate ==

This plugin source intentionally contains no production dataset. The local build/package verifier rejects missing, NOT_VERIFIED, unresolved, malformed, byte-count-mismatched, and SHA-256-mismatched imports. The optional permission metadata flag does not affect the gate.

Use shortcode:

[frpdepot_derakane_search]

== Safety and limitations ==

The interface exposes source edition/hashes, °C/°F units, exact Blank/NR/LS meanings, essential referenced footnotes, source pages, source row order, and the complete technical-limitations notice. It does not interpolate concentration or resin values and does not make material-selection decisions.
