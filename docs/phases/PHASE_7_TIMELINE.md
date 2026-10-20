# Phase 7 Timeline Events and Temporal Relationships

## Scope and dependency direction

`TimelineBuilder` accepts persisted `EvidenceRecord` values for exactly one
incident and returns a `Timeline`. It depends only on timeline/evidence
contracts, replaceable mapper and relationship-calculator ports, and
deterministic ID generation. `TimelinePersistenceService` depends on the
`TimelineRepository` port; it has no dependency on the in-memory adapter,
collection adapters, security implementation, context snapshots, hypotheses,
or an LLM.

Each immutable evidence record maps to exactly one timeline event containing
that record's evidence ID. The validator requires every supplied record to be
referenced once, rejects cross-incident records and dangling references, and
permits only deterministic Phase 7 relationship types.

## Ordering and time policy

Known event times sort first by UTC event time and then by evidence ID. Missing
event times are placed after every known event and sort by evidence ID. No
timestamp is inferred from detection, observation, or collection time.

`x-processing-metadata.time_uncertainty_ms`, when valid, is carried into the
timeline event. The repository preserves the same evidence-ID tie-breaker and
uses the event ID only as a final fallback for malformed/external equal-ID
events.

## Relationship policy

Only adjacent known-time events receive a simple temporal edge. Their
uncertainty intervals are compared with the configured coincidence window:

- intervals whose gap is at or below the window receive `COINCIDES_WITH`;
- otherwise the earlier event receives `PRECEDES` to the later event;
- records without event time receive no invented temporal edge.

`DEPLOYED_FROM` is emitted from a deployment event to a change event only when
both source records carry an equal, non-empty `revision` or `commit_sha`.
Its delta is calculated in code when both times are known. Relationship IDs are
derived from incident ID, endpoints, and type, and relationships are sorted by
that ID.

The builder never emits `CAUSES`, `SUPPORTS`, `CONTRADICTS`, or any other
reasoning-oriented relationship. Temporal proximity is represented as a
deterministic observation, not causal support.
