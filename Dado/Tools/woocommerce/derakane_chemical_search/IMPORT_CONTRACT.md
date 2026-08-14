# Closed Derakane search dataset import contract

Contract identifiers:

- Manifest: `frpdepot.derakane-search.import-manifest`, version `1`
- Dataset: `frpdepot.derakane-search.dataset`, version `1`

The JSON Schemas in `contracts/` are normative for shape. `import_contract.py` is the normative build/package gate and also enforces cross-file and semantic invariants that JSON Schema alone cannot express.

## Import placement

The rebuild pipeline must place exactly these files in the plugin source data directory:

```text
plugin/frpdepot-derakane-chemical-search/data/
├── import-manifest.json
└── derakane-dataset.json
```

`dataset.file` must be the basename `derakane-dataset.json`. Absolute paths, parent traversal, subdirectories, symlinks, and alternate filenames are rejected.

## Release gate

Build and package operations fail closed unless all of the following are true:

1. The manifest and dataset satisfy their closed schemas; unknown keys are rejected.
2. `producer.pipeline` is exactly `frpdepot.derakane-guide-rebuild` at producer contract version `1`.
3. `verification.status` is exactly `VERIFIED`.
4. `verification.unresolved_count` is exactly `0`.
5. Every row has `qa_status: VERIFIED`.
6. No cell/rating can represent an unresolved state; allowed rating states are only `blank`, `nr`, `ls`, and `value`.
7. Dataset byte length and SHA-256 exactly match the manifest pin.
8. Manifest and dataset source objects are identical, including source edition and original-document SHA-256.
9. Manifest summary counts match the imported content.
10. Source row sequence is strictly increasing and preserved as stored; it is never concentration-sorted.
11. Every row has the exact nine ordered resin cells and all footnote references resolve.

`permission_documented` is an optional manifest metadata field. It is deliberately not a release gate: missing, `false`, or `true` does not affect verification. No other permission field is accepted.

## Search semantics

- Chemical groups are ranked: exact chemical name → exact alias/CAS → starts-with → contains.
- Ties and all concentration rows retain source sequence.
- Concentration filtering is exact string equality against `concentration.display`; no ranges are parsed and no interpolation is permitted.
- Resin filtering selects one exact manifest-pinned resin column; it never substitutes or derives a value.
- Footnotes are resolved from the imported definitions and returned with every matching result that references them.

## Fixture boundary

`tests/fixtures/` contains conspicuously synthetic records solely for offline regression tests. Fixtures are not copied into the plugin `data/` directory and must never be packaged or deployed. The source data directory intentionally contains no manifest or dataset until a verified rebuild exists.
