# Derakane™ Resin Chemical Resistance Guide Search — local source

Fixed WordPress plugin source for a read-only guide search. It consumes only a manifest-pinned, zero-unresolved, verified dataset emitted by the closed rebuild-pipeline contract.

## Current state

- Source and fixture-only tests are complete.
- `plugin/frpdepot-derakane-chemical-search/data/` intentionally contains no production manifest or dataset.
- Therefore `verify`, `build`, and `package` currently return `BLOCKED`.
- No ZIP, deployment tool, staging plan, browser operation, website write, or email action is part of this source.

## Import contract

Read `IMPORT_CONTRACT.md` and the two schemas under `contracts/`. The complete semantic and cross-file gate is `import_contract.py`.

The optional `permission_documented` manifest field is metadata only. Missing, false, or true all pass when every data-integrity gate passes. Unknown substitute permission fields are rejected by the closed schema, but permission is never a build decision.

## Offline tests

All dataset records used by tests are conspicuously synthetic and live under `tests/fixtures/verified/`.

```bash
# PHP CLI must be available for the real PHP source harness.
python -m unittest discover -s tests -v
npm test
```

The suites cover:

- manifest absence, `NOT_VERIFIED`, unresolved records, hash/byte mismatch, source mismatch, count mismatch, unknown fields, invalid CAS checksums, row QA and unresolved rating rejection;
- permission flag absent/false/true not gating;
- exact nine resin columns, source row order, 27 resolved footnotes, source edition/document hash;
- exact-name → alias/CAS → starts-with → contains ranking;
- CAS and guide alias search, punctuation normalization, all known parsing/ordering regressions and golden fixtures;
- exact concentration/resin filtering without interpolation;
- distinct blank/NR/LS/value and split-grade cells;
- maximum 20 and Load more;
- `?chemical=` initial restore, push/replace history and popstate;
- `AbortController` cancellation and stale-response suppression;
- visible source hashes/edition, °C/°F, exact legend, complete limitations notice, per-result footnotes/source pages;
- visible labels, live status, captions, row/column scopes, focusable table region and mobile horizontal-scroll cue.

## Commands after the real rebuild import exists

```bash
python build_plugin.py verify
python build_plugin.py build
python build_plugin.py package
```

Do not run `package` before the production import exists and independently passes `verify`. The command cannot create an artifact before its verification gate succeeds.
