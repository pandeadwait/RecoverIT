# Phase 2 Repository Contract

## Ports and dependency direction

Application code depends on `EvidenceRepository`, `TimelineRepository`,
`ContextRepository`, and `RepositoryCoordinator`. The in-memory classes are
reference adapters, not product selections. Domain services do not import a
database library or vendor query object.

## Evidence identity and writes

Evidence IDs and their full contents are immutable. `save_all` has three rules:

1. A new ID is stored.
2. An existing ID with byte-identical canonical content is an idempotent success.
3. An existing ID with different canonical content raises
   `RepositoryConflictError` and stores none of that call's records.

`get` returns `None` when an ID does not exist. Queries are always isolated to
the incident in `EvidenceFilter`.

## Filtering, ordering, and pagination

`EvidenceFilter` remains the version 1.0 wire/domain filter. Its `limit` is the
maximum number of matching records visible to one query traversal. Event-time
ascending is the default order. Known event times sort chronologically, ties
sort by evidence ID, and unknown event times sort last by evidence ID. Unknown
times are excluded unless `include_unknown_event_time` is true.

`EvidencePageRequest` adds an adapter-neutral page size, opaque cursor, and
explicit ascending/descending order. Descending reverses event-time order but
keeps evidence-ID tie breaking ascending. Cursors must only be reused with the
same order and are valid while the queried repository contents remain
unchanged. Invalid cursors raise `RepositoryValidationError`.

## Timeline and context revisions

`replace_revision` atomically replaces the complete timeline stored at one
`(incident_id, revision)` key. It does not modify other revisions. Timeline
events are returned by event time, with unknown times last and event ID as the
tie breaker; relationships sort by relationship ID. `get_latest` selects the
greatest stored revision, never insertion order.

Snapshots are immutable by snapshot ID, and one incident revision maps to one
snapshot ID. Re-saving identical content is idempotent. `get_latest` likewise
selects the greatest revision and remains incident-isolated.

## Atomic context writes and failures

`RepositoryCoordinator.save_revision` is the unit-of-work boundary for saving
evidence, one timeline revision, and its context snapshot. All inputs must share
an incident, and timeline/snapshot revisions must match. Either all three stores
commit or all are restored to their prior state.

Adapters translate storage failures to `RepositoryError` subclasses. These can
be converted to the portable `ProcessingError` contract without exposing an
underlying storage exception or product type.

## Reusing the contract suite

Future adapters should subclass the `RepositoryContract` mixin in
`tests/repository_contract.py`, implement `create_repositories`, and run the
same suite. The suite covers idempotency, immutable-ID conflicts, atomic batch
saves, not-found behavior, filtering, stable ordering, pagination, incident
isolation, revision replacement, and latest-revision selection.
