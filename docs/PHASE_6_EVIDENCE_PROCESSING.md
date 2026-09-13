# Phase 6 Evidence Processing and Persistence

## Dependency direction

`EvidenceProcessingService` is the application orchestration boundary. It
depends on the Phase 3 normalization service, Phase 4 security service, Phase 5
deduplication service, `RepositoryCoordinator` port, and `EvidenceAuditSink`
port. It does not import an in-memory adapter, source collector, timeline
builder, database library, framework, or reasoning component.

The service accepts validated `IncidentSeed` and `RawEvidenceBatch` contracts.
It sorts and de-duplicates batch envelopes, then runs normalization, security,
and deduplication before constructing immutable `EvidenceRecord` values.

## Persistence and replay safety

Evidence records, repeated-event state, and provenance attachments are saved by
one coordinator operation. The in-memory reference coordinator snapshots all
three stores and restores them if any write fails. Database implementations
must provide equivalent transaction behavior.

Repeated-event state is stored through `DeduplicationStateRepository`, separate
from `EvidenceRepository`. This preserves immutable evidence while allowing a
log aggregate to retain its current count, first/last event time, truncation
indicator, and every contributing provenance record. Replaying a contributor
uses the stored provenance attachment and does not increment the aggregate.

Deterministic IDs and idempotent repository writes make concurrent identical
requests safe. A batch ID repeated with different content is rejected before
processing because it violates envelope integrity.

## Failure classification

| Failure | Result | Retryable |
|---|---|---|
| Empty/conflicting envelope | `invalid_envelope` fatal error | no |
| Cross-incident batch | `incident_mismatch` fatal error | no |
| Redaction, hashing, quality, or deduplication policy failure | `policy_rejected` fatal error | no |
| Repository/transaction failure | `storage_failure` fatal error | adapter-defined |
| Malformed independent record | `malformed_record` warning | processing continues |
| Quarantined record | `x-quarantined` warning and safe quarantine metadata | processing continues |
| Source collection error | preserved in `source_errors` | processing continues for valid records |
| Audit sink failure | `x-observability-failure` warning | stored evidence remains valid |

Fatal application exceptions expose only the portable `ProcessingError`
contract. Adapter and policy exception text is never reflected to callers.

## Safe observability

Audit events contain incident and batch identifiers, canonical evidence IDs,
fixed action/reason codes, and quarantine reason codes. They never include raw
payloads, summaries, evidence attributes, or exception text. The default sink
is a no-op and the in-memory sink is supplied for contract and integration
tests; production composition can replace it without changing domain behavior.
The production audit adapter is responsible for adding the architecture-level
event ID, actor, UTC timestamp, correlation/causation IDs, and hash-chain
envelope. Keeping that concern in the adapter prevents wall-clock time and
storage-specific audit types from making application results nondeterministic.

The processing result returns saved, reused, and aggregated evidence IDs;
structured warnings and non-fatal source errors; quarantine metadata;
deduplication decisions; aggregation state; and provenance attachments for
later timeline construction.
