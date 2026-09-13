# Phase 8 Incident Context Publication

## Publication boundary

`ContextPublicationService` publishes the immutable contract consumed by Person 3.
It reads canonical evidence, the latest persisted timeline, and the latest context
revision only through repository ports. It writes the matching timeline and
context revision through one atomic coordinator operation. The service has no
database, in-memory adapter, collection-adapter, hypothesis, or LLM dependency.

`ContextSnapshotBuilder` is pure apart from its injected clock and ID generator.
For fixed inputs it produces stable evidence ordering, timeline ordering, warning
ordering, and snapshot IDs. Revision 1 is used when no prior snapshot exists;
each later snapshot uses the prior revision plus one. Prior snapshots are never
mutated or overwritten.

## Source coverage

Coverage is calculated from collection execution statuses, independently for all
six supported source types:

- `available`: at least one current result contains records;
- `empty`: a current `ok` or `partial` result contains no records;
- `unavailable`: current results failed or reported unavailable and contain no
  records;
- `not_queried`: no current result exists and there is no prior coverage state.

When a source is not queried during a later publication, its previous state is
preserved. This prevents an incremental query from falsely erasing known
coverage. Only sanitized processing warnings supplied to the publication service
are merged into a snapshot; raw collection payloads and raw adapter errors do not
cross this boundary.

## Person 3 public interface

Person 3 should depend on `IncidentContextReader`, not a repository adapter.
`get_latest_context(incident_id)` returns the latest immutable snapshot,
`get_evidence(evidence_id)` resolves a compact projection to its complete
canonical record, and `query_evidence(filter)` performs domain-level evidence
lookup. Replacing a persistence adapter therefore does not change the consumer
interface or serialized snapshot contract.

Person 3 may assume:

- every snapshot, timeline, event, and evidence projection belongs to one
  incident;
- every persisted evidence record appears exactly once in the timeline and can
  be resolved by its evidence ID;
- evidence projections contain only compact, redacted, consumer-safe fields;
- known times precede unknown times and deterministic IDs break ties;
- coverage distinguishes an empty response from an unattempted or unavailable
  source;
- warnings are deterministic, deduplicated, and retained across revisions.

Person 3 must continue to treat these as unknown:

- temporal proximity is not causation and timeline edges are not hypotheses;
- an unavailable, empty, or unqueried source says nothing about what evidence
  would exist if collection succeeded;
- missing event time must remain unknown rather than being inferred;
- freshness and reliability metadata describe evidence quality, not truth;
- the snapshot contains no ranked root cause, causal support score, or agent
  conclusion.

The LLM/agentic investigation layer belongs after this boundary. It can reason
over the snapshot and use `IncidentContextReader` to request full records, while
Person 2 remains deterministic and auditable.
