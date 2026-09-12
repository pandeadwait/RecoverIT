# Phase 5 Deterministic Identity, Deduplication, and Aggregation

## Evidence identities

`DeterministicEvidenceIdStrategy` receives an `IdGenerator` port and derives
the same evidence ID for the same incident and source identity. It never uses
batch ID, query ID, collection time, or input order.

| Source category | Identity material |
|---|---|
| changes | revision |
| deployments | deployment ID |
| pipelines | pipeline-run ID |
| configuration | configuration-change ID |
| logs | opaque aggregation-key hash when event time exists; otherwise source-record ID plus raw-payload hash |
| metrics | source-record ID plus raw-payload hash |

Exact matching always uses `(source_type, source_record_id, raw_payload_hash)`.
Thus replaying a source record from a later batch reuses the target evidence ID.
Non-log source records can also reuse a target through their documented stable
external identity. Equal non-log content with different external IDs remains
distinct.

## Repeated log observations

`LogAggregationPolicy` makes a non-revealing SHA-256 signature from the safe
normalized summary, evidence type, service, and selected diagnostic attributes.
It groups only logs that have the same service, signature, and configured UTC
time bucket. The reference bucket is 300 seconds and can be replaced.

`RepeatedEventAggregate` retains count, first/last event time, truncation, and
every contributing provenance record. The aggregate key is an opaque digest;
neither raw payload nor readable signature material appears in the audit output.

## Immutable evidence and Phase 6 handoff

Existing `EvidenceRecord` values are immutable by contract. Therefore Phase 5
does not rewrite them when a later repeated log is found. Instead it emits an
aggregation sidecar and `ProvenanceAttachment` audit records, which Phase 6 can
persist transactionally with the immutable evidence write. This retains each
observation and provenance link without creating duplicate evidence IDs.

`DeduplicationDecision` explicitly records one of `create`, `reuse`,
`aggregate`, or `reject`. All candidate, repository-match, and output ordering
is stable, so input permutations produce identical decision and aggregation
results.
