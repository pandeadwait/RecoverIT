# Phase 1 Contract Compatibility

## Status

Contract version `1.0` is the first integration version for Person 2. These Python dataclasses are a reference implementation of portable wire contracts; they do not select a database, framework, source adapter, or reasoning provider.

## Compatibility policy

- Every serialized output includes `schema_version: "1.0"`.
- A receiver rejects an unsupported schema version at the envelope boundary.
- Additive optional fields are compatible when consumers ignore unknown fields.
- Removing a field, changing a required field, changing a field type, or changing a field's meaning requires a new schema version.
- Closed enums reject unsupported values. Extensible enums safely preserve `"unknown"` and values beginning with `"x-"` for explicitly namespaced extensions.
- IDs, incident IDs, provenance, and schema version are immutable once a record is created. Repository enforcement arrives in Phase 2.

## Validation boundary

- `IncidentSeed` and `RawEvidenceBatch` are validated before Person 2 processing begins.
- Invalid envelopes are rejected with `ContractValidationError`.
- Later processing phases may convert malformed individual records into `ProcessingWarning` or `ProcessingError` without discarding valid sibling records.
- No raw payload is persisted by this phase. Future normalization must hash raw data in memory and redact it before it reaches a Person 2 output boundary.

## Determinism

- Canonical JSON uses UTF-8, sorted object keys, compact separators, and rejects non-finite numeric values.
- All wire timestamps are emitted in UTC using the `Z` suffix.
- `Clock`, `IdGenerator`, and `PayloadHasher` are ports. `FixedClock`, `DeterministicIdGenerator`, and `Sha256PayloadHasher` are test/reference implementations only.

## Person 1 to Person 2

Person 2 consumes only serialized `IncidentSeed` and `RawEvidenceBatch` contracts. It must not import a Person 1 collector or adapter.

## Person 2 to Person 3

Person 3 will receive serialized `IncidentContextSnapshot` data and, in Phase 2, the domain-owned evidence read interface. Person 3 must not import a database package or Person 2 normalization internals.
