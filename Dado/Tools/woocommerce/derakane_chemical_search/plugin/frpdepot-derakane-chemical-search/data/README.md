# Verified rebuild output only

Do not place hand-authored, audit-extracted, fixture, unresolved, or `NOT_VERIFIED` data here.

The rebuild pipeline must atomically supply `import-manifest.json` and the manifest-pinned `derakane-dataset.json`. `build_plugin.py` and the runtime loader both fail closed when either file is absent, unresolved, not verified, malformed, or hash-mismatched.

Test fixtures live outside the plugin tree under `tests/fixtures/` and are never package inputs.
