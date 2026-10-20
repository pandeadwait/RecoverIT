# Phase 1 Test Suite

Run the complete Phase 1 suite from the repository root:

```sh
pjtVenv/bin/python -m unittest discover -s tests -v
```

The suite is dependency-free and uses only Python's standard library. It covers:

- JSON round trips for every Person 2 Phase 1 output contract.
- Valid input-boundary examples for `IncidentSeed` and `RawEvidenceBatch`.
- Valid and invalid schema fixtures.
- Required-field, enum, timestamp, timezone, and time-range validation.
- Additive optional-field compatibility and `x-` extension handling.
- Cross-incident, missing-reference, and source-coverage integrity checks.
- Immutability of IDs, provenance, attributes, and source coverage.
- Stable canonical JSON/bytes, fixed clock behavior, deterministic IDs, and payload hashing.
- Standard JSON serialization of every published output fixture.

For an environment consistency check, run:

```sh
pjtVenv/bin/python -m pip check
```

The `pjtVenv/` directory is intentionally excluded from version control.
