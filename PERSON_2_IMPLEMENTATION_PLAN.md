# Person 2 — Phase-wise Implementation Plan

## Evidence Normalization, Storage, and Timeline

| Item | Value |
|---|---|
| Owner | Person 2 |
| Status | Planning only; no implementation is included in this document |
| Input boundary | `IncidentSeed`, one or more `RawEvidenceBatch` objects, and optionally a previous `IncidentContextSnapshot` |
| Output boundary | `IncidentContextSnapshot` plus evidence and timeline read interfaces |
| Primary consumers | Person 3's investigation, hypothesis, evidence-citation, and ranking modules |
| Explicit stopping point | Person 2 does not generate, evaluate, or rank hypotheses |

---

## 1. Goal

Build the technology-neutral evidence-processing layer that converts heterogeneous collected records into safe, canonical, traceable evidence and a deterministic chronological incident timeline.

The implementation must allow the database, serialization library, application framework, identifier generator, clock, hashing algorithm, redaction implementation, and configuration source to be changed without rewriting domain behavior.

The completed Person 2 subsystem will perform this flow:

```text
IncidentSeed + RawEvidenceBatch(es) + optional previous snapshot
    -> validate inputs
    -> classify source records
    -> normalize timestamps and identities
    -> hash the original payload in memory
    -> redact sensitive data
    -> deduplicate and aggregate repeated observations
    -> create canonical EvidenceRecord objects
    -> persist evidence through an abstract repository
    -> create TimelineEvent objects
    -> calculate safe temporal relationships
    -> assemble and persist IncidentContextSnapshot
    -> expose evidence lookup and context queries to Person 3
```

The original unredacted payload must not be persisted, logged, returned, or sent to a reasoning provider.

---

## 2. Scope Boundaries

### 2.1 In scope

- Versioned evidence, timeline, relationship, filter, and snapshot contracts.
- Runtime validation of Person 2 inputs and outputs.
- Source-record classification for logs, metrics, Git changes, deployments, pipelines, and configuration changes.
- UTC timestamp normalization with uncertainty metadata.
- Canonical service and resource identity mapping.
- Raw-record hashing without retaining unredacted content.
- Secret and configurable sensitive-data redaction.
- Deterministic evidence identifiers.
- Idempotent deduplication and repeated-event aggregation.
- Reliability, freshness, truncation, and provenance metadata.
- Replaceable repositories and in-memory reference implementations.
- Deterministic timeline construction and non-causal temporal relationships.
- Snapshot revisioning and source-coverage calculation.
- Evidence lookup and context-query services.
- Recorded fixtures and automated tests.

### 2.2 Out of scope

- Connecting directly to live log, metric, Git, deployment, pipeline, or configuration systems.
- Choosing what information to collect next.
- Calling an LLM or other reasoning provider.
- Generating or ranking root-cause hypotheses.
- Treating temporal proximity as proof of causation.
- Remediation planning or execution.
- A production database migration or vendor-specific deployment unless separately selected later.

---

## 3. Design Rules for Replaceability

All implementation choices are hidden behind small contracts. Concrete products are selected in the application composition layer, not imported by domain services.

| Concern | Abstract contract | Initial reference implementation | Replaceable later with |
|---|---|---|---|
| Evidence persistence | `EvidenceRepository` | In-memory repository | SQL, document DB, graph DB, or object-backed store |
| Timeline persistence | `TimelineRepository` | In-memory repository | Any transactional or event-oriented store |
| Snapshot persistence | `ContextRepository` | In-memory repository | Any database or document store |
| Serialization/validation | Versioned contract codec | Project-selected schema validator | Another validation library or language |
| Time | `Clock` | System UTC clock; fixed clock in tests | Framework or distributed clock abstraction |
| IDs | `IdGenerator` | Deterministic IDs for evidence; generated IDs elsewhere | UUID, ULID, content ID, or database IDs |
| Hashing | `PayloadHasher` | Configured secure digest | Another approved digest implementation |
| Redaction | `RedactionPolicy`/`Redactor` | Pattern and field policy | DLP service, policy engine, or organization-specific rules |
| Identity mapping | `IdentityResolver` | Configuration-backed aliases | Service catalog or CMDB adapter |
| Reliability | `QualityPolicy` | Configuration-backed rules | Learned or organization-specific policy |
| Transaction boundary | `UnitOfWork` or repository coordinator | In-memory atomic operation | Database transaction/session |
| Configuration | Typed settings interface | Static/test settings | File, environment, secrets manager, or remote configuration |
| Observability | Structured event sink | No-op/in-memory test sink | Logs, traces, metrics, or audit platform |

Rules:

1. Domain models contain no database annotations or vendor-specific types.
2. Repository filters are domain-owned value objects, not database query objects.
3. Services receive clocks, ID generators, policies, and repositories through constructors or equivalent dependency injection.
4. Deterministic logic must not depend on wall-clock time, iteration order, locale, or database return order.
5. Contract JSON examples remain portable across languages.
6. Product selections require only an adapter and the shared contract test suite.

---

## 4. Proposed Module Boundaries

The exact programming language and framework may be chosen later. The logical structure should remain:

```text
contracts/
  evidence/                 EvidenceRecord, EvidenceFilter, quality, provenance
  timeline/                 TimelineEvent, TemporalRelationship, Timeline
  context/                  IncidentContextSnapshot and source coverage
  errors/                   Structured validation and processing errors

evidence/
  application/              Processing orchestration and query services
  normalization/            Classification, timestamps, identity, summaries
  security/                 Payload hashing, redaction, quarantine decisions
  deduplication/            Exact duplicate and repeated-event policies
  quality/                  Reliability, freshness, and truncation policies
  repositories/             Ports and in-memory adapters
  context/                  Snapshot assembly and revisioning

timeline/
  builder/                  Evidence-to-event conversion and stable ordering
  relationships/            PRECEDES, COINCIDES_WITH, DEPLOYED_FROM rules
  repositories/             Timeline repository port and in-memory adapter

tests/
  unit/
  contract/
  integration/
  replay/
  fixtures/
```

Dependencies must point inward: adapters depend on application/domain contracts; domain code never depends on adapters.

---

## 5. Core Service Contracts

Names are illustrative. The signatures define responsibilities, not a mandatory language.

```text
EvidenceProcessingService.process(
    incident: IncidentSeed,
    batches: list[RawEvidenceBatch],
    previous_snapshot: IncidentContextSnapshot?
) -> IncidentContextSnapshot

EvidenceNormalizer.normalize(
    incident: IncidentSeed,
    batch_context: BatchContext,
    raw_record: RawSourceRecord
) -> NormalizedEvidenceCandidate | QuarantinedRecord

Redactor.redact(candidate) -> RedactionResult
Deduplicator.resolve(candidate, existing_matches) -> DeduplicationDecision
TimelineBuilder.build(incident_id, evidence_records) -> Timeline
ContextSnapshotBuilder.build(incident, evidence, timeline, previous_snapshot) -> IncidentContextSnapshot

EvidenceRepository.save_all(records) -> list[evidence_id]
EvidenceRepository.get(evidence_id) -> EvidenceRecord?
EvidenceRepository.query(filter: EvidenceFilter) -> list[EvidenceRecord]

TimelineRepository.replace_revision(incident_id, revision, events, relationships) -> void
TimelineRepository.get_latest(incident_id) -> Timeline?

ContextRepository.save(snapshot) -> snapshot_id
ContextRepository.get_latest(incident_id) -> IncidentContextSnapshot?
```

All methods must return or raise project-defined error types. Storage-library exceptions must be translated at the adapter boundary.

---

## 6. Phase Plan

### Phase 0 — Contract alignment and decision register

**Purpose:** Freeze the boundaries needed for independent work before writing production code.

**Inputs**

- `ARCHITECTURE.md`.
- `WORK_DIVISION.md`.
- Person 1's `IncidentSeed` and `RawEvidenceBatch` v1.0 examples.
- Person 3's required `IncidentContextSnapshot` fields and evidence lookup needs.

**Work**

1. Review all identifiers, timestamps, enumerations, nullability rules, and error formats with Persons 1 and 3.
2. Confirm supported source types: logs, metrics, changes, deployments, pipelines, and configuration.
3. Define compatibility rules for additive and breaking schema changes.
4. Agree on the minimum compact evidence projection required inside a snapshot.
5. Agree on the `EvidenceFilter` fields Person 3 may use without database knowledge.
6. Record unresolved choices as ADRs with abstract alternatives; do not embed a product selection in domain contracts.
7. Freeze contract version `1.0` for the first integration milestone.

**Outputs**

- Contract checklist and compatibility note.
- Agreed v1.0 JSON examples.
- ADR placeholders for language, storage adapter, schema validator, and packaging.
- A mapping table from every `RawEvidenceBatch.source_type` to expected canonical evidence categories.

**Tests/review**

- All example inputs and outputs round-trip through the selected schema representation.
- Person 1 confirms it can produce the input contract.
- Person 3 confirms it can consume the snapshot and repository read contract.

**Exit criteria**

- No unresolved ambiguity prevents schema implementation.
- Contract owners approve v1.0.

---

### Phase 1 — Domain contracts and deterministic primitives

**Purpose:** Implement the portable types and low-level deterministic abstractions used by every later phase.

**Inputs**

- Frozen v1.0 contracts from Phase 0.

**Work**

1. Define and validate:
   - `EvidenceRecord`.
   - `EvidenceProvenance`.
   - `EvidenceQuality`.
   - `EvidenceFilter`.
   - `TimelineEvent`.
   - `TemporalRelationship`.
   - `Timeline`.
   - `IncidentContextSnapshot`.
   - Source-coverage states.
   - Structured processing warnings and errors.
2. Define domain enumerations while preserving a safe `unknown`/extension path where appropriate.
3. Define `Clock`, `IdGenerator`, `PayloadHasher`, and canonical JSON/byte encoding contracts.
4. Specify immutable-field rules for evidence IDs, incident IDs, provenance, and schema versions.
5. Define validation boundaries: reject invalid envelopes; quarantine or warn on malformed individual records where safe.
6. Add example fixtures for every output contract.

**Outputs**

- Versioned contract package.
- Deterministic utility interfaces.
- Valid and invalid schema fixtures.
- Contract compatibility notes.

**Tests**

- Required fields, enumerations, and timestamp formats.
- JSON round-trip tests.
- Rejection of invalid schema versions and cross-incident references.
- Stable canonical byte encoding for the same logical payload.
- Unknown optional fields handled according to compatibility policy.

**Exit criteria**

- Contracts contain no database, LLM, web framework, or source-adapter types.
- Fixed inputs serialize identically across repeated runs.

---

### Phase 2 — Repository ports and in-memory reference adapters

**Purpose:** Establish persistence behavior before the processing pipeline depends on it.

**Inputs**

- Phase 1 domain contracts.

**Work**

1. Define `EvidenceRepository`, `TimelineRepository`, and `ContextRepository` ports.
2. Define domain-owned filter and pagination/order semantics.
3. Decide and document save behavior for an existing immutable evidence ID.
4. Define atomicity requirements for saving evidence, timeline revision, and context snapshot.
5. Build in-memory adapters as the executable reference behavior.
6. Build one reusable repository contract suite that any future database adapter must pass.
7. Ensure repository results use explicit stable ordering.

**Outputs**

- Repository interfaces.
- In-memory implementations.
- Reusable repository contract-test suite.
- Storage failure/error translation contract.

**Tests**

- Save, retrieve, query, and not-found behavior.
- Idempotent save of an existing identical record.
- Rejection of an incompatible record with the same immutable ID.
- Incident isolation.
- Stable query ordering.
- Latest timeline/snapshot revision behavior.
- Replace-revision semantics.

**Exit criteria**

- Application services can use repositories without importing an adapter package.
- A second adapter could be introduced by implementing the ports and passing the same tests.

---

### Phase 3 — Classification and canonical normalization

**Purpose:** Convert each supported raw source record into a canonical, storage-ready candidate.

**Inputs**

- `IncidentSeed`.
- Valid `RawEvidenceBatch` fixtures covering all six source categories.
- Phase 1 contracts.

**Work**

1. Validate batch-to-incident consistency and required collection metadata.
2. Route raw records using a source-type classifier registry.
3. Implement source-neutral classifier interfaces and one classifier per required source category.
4. Normalize timestamps to UTC while preserving original values/timezone metadata when supplied.
5. Distinguish `event_time`, `observed_at`, and `collected_at`.
6. Represent missing or approximate event times explicitly; never invent an exact source time.
7. Resolve service/resource aliases through `IdentityResolver`.
8. Create concise deterministic summaries and structured attributes without an LLM.
9. Attach source status, truncation, and malformed-record warnings.
10. Produce `NormalizedEvidenceCandidate`; do not persist it yet.

**Outputs**

- Classifier registry and six source classifiers.
- Timestamp normalizer.
- Identity resolver port plus configuration-backed reference adapter.
- Deterministic summary/attribute extraction.
- Normalized candidate fixtures.

**Tests**

- Every source category maps to a canonical candidate.
- UTC conversion across offsets and daylight-saving boundaries.
- Missing, malformed, and approximate event times.
- Unknown service aliases and explicit fallback behavior.
- Batch incident mismatch rejection.
- Partial/truncated source metadata preservation.
- Malformed record isolation without losing valid siblings.

**Exit criteria**

- Every valid fixture produces the expected deterministic candidate.
- No source-specific payload type leaks into downstream interfaces.

---

### Phase 4 — Security, provenance, and quality metadata

**Purpose:** Guarantee that evidence is safe and traceable before it crosses the persistence or reasoning boundary.

**Inputs**

- Normalized evidence candidates from Phase 3.
- Configured redaction and quality policies.

**Work**

1. Canonicalize the original raw payload in memory and calculate its configured secure hash.
2. Immediately discard the unredacted canonical bytes after hashing.
3. Redact secrets in both keys and values, including nested structures and free text.
4. Cover bearer tokens, API keys, passwords, private keys, database URLs, cloud credentials, secret-valued environment variables, and configured personal identifiers.
5. Define policy outcomes: pass, redact, or quarantine.
6. Prevent sensitive values from appearing in warnings, exceptions, or observability events.
7. Attach provenance: batch, query, source record, source adapter, and raw payload hash.
8. Calculate freshness from explicit timestamps using the injected clock or batch collection time as defined by policy.
9. Assign reliability using configurable, explainable rules rather than hard-coded vendor assumptions.
10. Produce a safe candidate that is permitted to enter deduplication and persistence.

**Outputs**

- `PayloadHasher` adapter.
- Redaction policy and redactor.
- Quarantine result contract.
- Provenance builder.
- Quality policy and calculator.
- Synthetic security fixtures.

**Tests**

- Nested and encoded synthetic secret patterns.
- Secrets absent from outputs, error messages, and captured test logs.
- Hash remains stable for canonical-equivalent input.
- Redaction does not destroy provenance.
- Freshness boundary and clock-skew cases.
- Truncation and reliability rules.
- Quarantined content never reaches repositories or snapshots.

**Exit criteria**

- Only redacted, policy-approved content can leave the phase.
- Every approved candidate has complete provenance and quality metadata.

---

### Phase 5 — Deterministic identity, deduplication, and aggregation

**Purpose:** Make batch replay idempotent while preserving the meaning and provenance of repeated observations.

**Inputs**

- Safe candidates from Phase 4.
- Existing evidence matches returned through `EvidenceRepository`.
- Configured log aggregation time bucket and signature rules.

**Work**

1. Define deterministic evidence identity material per source type.
2. Detect exact duplicates by source, stable source-record ID, and payload hash.
3. Detect replayed batches without creating new evidence.
4. Deduplicate deployments, commits, pipeline runs, and configuration changes using stable external IDs.
5. Aggregate repeated logs only when normalized signature, service, and configured time bucket match.
6. Preserve occurrence count, first/last event time, contributing provenance, and truncation indicators.
7. Ensure hash or signature comparisons do not reveal redacted values.
8. Produce an explicit decision: create, reuse, aggregate, or quarantine/reject.
9. Guarantee deterministic output regardless of input record ordering.

**Outputs**

- Evidence ID strategy.
- Deduplication policy and service.
- Repeated-event aggregation model.
- Deduplication decision/audit metadata.

**Tests**

- Same batch replayed once or many times.
- Same record appearing in different batches.
- Equal content with different stable source IDs according to documented policy.
- Repeated logs inside and outside the aggregation bucket.
- Same deployment/commit from multiple collection queries.
- Input-order permutation/property tests.
- Provenance remains traceable after reuse or aggregation.

**Exit criteria**

- Reprocessing identical input changes neither evidence count nor canonical IDs.
- Aggregation is deterministic and never silently discards provenance.

---

### Phase 6 — Evidence processing orchestration and persistence

**Purpose:** Compose Phases 3–5 into the main evidence ingestion use case and persist complete `EvidenceRecord` objects.

**Inputs**

- `IncidentSeed`.
- One or more validated `RawEvidenceBatch` objects.
- Classifiers, policies, repositories, clock, hasher, and ID generator.

**Work**

1. Implement the `EvidenceProcessingService` orchestration path.
2. Process independent records safely while ensuring deterministic final ordering.
3. Convert approved deduplication decisions into canonical `EvidenceRecord` writes.
4. Use the repository transaction/coordinator boundary to prevent partial inconsistent state.
5. Convert record-level failures into structured warnings when processing can continue.
6. Fail the batch only for envelope, incident-integrity, policy, or storage errors defined as fatal.
7. Emit safe structured audit/observability events.
8. Return saved/reused evidence IDs and processing warnings for timeline construction.

**Outputs**

- End-to-end evidence-normalization pipeline.
- Processing result contract.
- Error classification and retry-safety documentation.
- Persisted canonical evidence fixtures.

**Tests**

- Mixed source batch processing.
- Multi-batch processing for one incident.
- Partial malformed results alongside valid records.
- Repository failure and atomicity behavior.
- Concurrent/repeated request idempotency where supported by the adapter contract.
- No secret exposure in persistence or telemetry.
- Full evidence retrieval by immutable ID.

**Exit criteria**

- Any valid input batch produces retrievable canonical records.
- Replayed input is idempotent.
- Fatal and recoverable failures have documented, structured behavior.

---

### Phase 7 — Timeline events and temporal relationships

**Purpose:** Build a reproducible operational history without making unsupported causal claims.

**Inputs**

- Persisted `EvidenceRecord` objects for one incident.
- Incident detection time.
- Timeline rule configuration.

**Work**

1. Map evidence types to timeline categories: change, deployment, symptom, alert, action, or verification. Current Person 2 inputs will normally use the first four; the model remains extensible.
2. Build timeline events with references to existing evidence IDs only.
3. Sort primarily by `event_time`; use explicit time uncertainty and deterministic evidence-ID tie-breaking.
4. Define placement policy for records with unknown event times without inventing timestamps.
5. Calculate deltas in code.
6. Create `PRECEDES` and `COINCIDES_WITH` relationships using configured deterministic rules.
7. Create `DEPLOYED_FROM` only when a shared revision/deployment identifier exists.
8. Do not emit `CAUSES`; do not turn time proximity into support or contradiction.
9. Validate graph integrity: incident consistency, existing endpoints, no invalid self-links, and deterministic relationship IDs.
10. Persist the complete timeline as a replaceable revision.

**Outputs**

- Evidence-to-timeline mapper.
- Stable timeline builder.
- Temporal relationship calculators.
- Timeline validator.
- Persisted timeline revision.

**Tests**

- Stable ordering for equal timestamps.
- Different source clocks and uncertainty windows.
- Missing event times.
- Deterministic deltas and relationship IDs.
- `COINCIDES_WITH` boundary conditions.
- `DEPLOYED_FROM` with and without a real shared identifier.
- Explicit test proving no causal edge is inferred from timing alone.
- Every event and relationship resolves within the same incident.

**Exit criteria**

- Fixed evidence produces byte-equivalent timeline output across runs.
- Every timeline reference resolves to stored evidence.
- No unsupported causal relationship is present.

---

### Phase 8 — Context snapshot and read/query services

**Purpose:** Publish the stable Person 2 output consumed by Person 3.

**Inputs**

- `IncidentSeed`.
- Persisted evidence.
- Latest persisted timeline.
- Optional previous snapshot.
- Collection source statuses from processed batches.

**Work**

1. Build compact evidence projections while retaining full records in `EvidenceRepository`.
2. Calculate source coverage separately for `available`, `empty`, `not_queried`, and `unavailable`.
3. Merge warnings deterministically and remove duplicates without losing their source.
4. Increment snapshot revision deterministically from the previous persisted revision.
5. Validate snapshot references and incident consistency before save.
6. Save the immutable snapshot revision.
7. Implement evidence lookup by ID and domain-filtered evidence queries.
8. Implement latest-context lookup without exposing a database API.
9. Document exactly what Person 3 may assume and what remains unknown.

**Outputs**

- `ContextSnapshotBuilder`.
- Source-coverage calculator.
- Context repository adapter behavior.
- Evidence lookup/context-query application service.
- Final `IncidentContextSnapshot` fixtures.

**Tests**

- First snapshot and subsequent deterministic revision numbers.
- Source coverage for all four states.
- Compact references resolve to full evidence.
- Snapshot cannot cite evidence from another incident.
- Warning merge/deduplication.
- Stable evidence/timeline ordering in serialized snapshots.
- Person 3 consumer contract tests using only public interfaces.

**Exit criteria**

- Person 3 can consume the snapshot and resolve evidence without database knowledge.
- Replacing repository adapters does not change snapshot contents for fixed input.

---

### Phase 9 — Integration, replay, hardening, and handoff

**Purpose:** Prove Person 2 works independently and across the two team boundaries.

**Inputs**

- Recorded Person 1 output fixtures.
- Prepared Person 3 consumer tests.
- Completed Person 2 modules.

**Work**

1. Run Person 1 -> Person 2 contract tests using serialized `IncidentSeed` and `RawEvidenceBatch` only.
2. Run Person 2 -> Person 3 contract tests using `IncidentContextSnapshot` and `EvidenceRepository` read methods only.
3. Replay scenarios for:
   - Deployment configuration regression.
   - Memory exhaustion.
   - Dependency incompatibility.
   - Database outage.
   - Coincidental deployment that is not causal.
4. Run repeated and permuted replays to prove determinism and idempotency.
5. Run security tests proving synthetic secrets never cross Person 2's output boundary.
6. Run repository contract tests against the in-memory adapter and any optional selected adapter.
7. Measure processing time, evidence volume, duplicate ratio, warning count, and snapshot size without making these metrics domain dependencies.
8. Document extension procedures for a new evidence type, redaction rule, identity source, and repository adapter.
9. Prepare examples and a short integration guide for Persons 1 and 3.

**Outputs**

- Pairwise contract-test results.
- Deterministic replay fixtures and expected outputs.
- Security and failure-path test results.
- Extension and integration guide.
- Person 2 handoff checklist.

**Tests**

- Complete Person 2 unit, contract, integration, replay, and property-test suites.
- Full pipeline from recorded raw batch to stored context snapshot.
- Fixed inputs produce identical IDs, records, ordering, relationships, warnings, and snapshot revision behavior.

**Exit criteria**

- Every acceptance criterion in `WORK_DIVISION.md` section 7.13 passes.
- Persons 1 and 3 can integrate using contracts and fixtures only.
- No code within Person 2 generates hypotheses, assigns support/contradiction, calls an LLM, or ranks causes.

---

## 7. Recommended Development Sequence

The phases should be implemented in order because each creates contracts or behavior required by the next phase. Small vertical slices should still be completed early.

1. Complete Phases 0–2 to stabilize contracts and testing infrastructure.
2. In Phase 3, implement one log fixture end to end through a normalized candidate.
3. Complete Phase 4 before allowing any candidate into persistence.
4. Add Phase 5 idempotency before composing the persistent pipeline.
5. In Phase 6, finish a log-only vertical slice from batch to stored evidence.
6. Add the other five source classifiers through the same interfaces.
7. Build timeline and snapshot behavior in Phases 7–8.
8. Complete pairwise and replay hardening in Phase 9.

This sequence exposes integration mistakes early while preserving the final modular boundaries.

---

## 8. Test Strategy by Level

| Test level | Main purpose | Representative coverage |
|---|---|---|
| Unit | Prove deterministic rules in isolation | Timestamp conversion, redaction, hashing, identity mapping, signatures, ordering, deltas, coverage states |
| Contract | Prove replaceability and team boundaries | Schema round trips, repository adapters, Person 1 input, Person 3 output |
| Property | Explore large input variations | Input permutations, replay idempotency, timestamp edge cases, secret strings, duplicate groups |
| Integration | Prove module composition | Batch -> evidence repository -> timeline -> snapshot |
| Replay/golden | Prove repeatability | Recorded incidents produce reviewed canonical outputs |
| Security | Prove boundary protection | No synthetic secrets in storage, errors, telemetry, fixtures, or snapshots |
| Failure-path | Prove safe degradation | Malformed record, partial batch, unavailable source, repository failure, policy quarantine |
| Performance | Detect unacceptable scaling | Large batches, repeated logs, query latency, snapshot size |

Golden fixtures should be reviewed but not used as the only assertion style. Tests should also assert invariants such as incident isolation, reference integrity, absence of secrets, chronological ordering, and idempotency.

---

## 9. Minimum Fixture Set

Person 2 can work independently using recorded data. The fixture suite should include:

1. One valid `IncidentSeed`.
2. One batch per each of the six source types.
3. One mixed-source batch.
4. One partial/truncated result.
5. One unavailable source result.
6. One malformed record alongside valid records.
7. Records with different timezone offsets.
8. Missing and approximate event times.
9. Exact duplicates across batches.
10. Repeated logs inside and outside the aggregation window.
11. A commit, pipeline, and deployment sharing a revision identifier.
12. A nearby deployment with no shared identifier, to prove no causal relationship is inferred.
13. Nested synthetic secrets and personal identifiers.
14. Unknown service aliases.
15. A previous snapshot used to create the next revision.

Each fixture must declare its expected evidence count, warnings, coverage states, timeline order, relationships, and snapshot revision.

---

## 10. Decisions to Record Without Locking the Architecture

These choices can be made during implementation and later replaced. Each should be captured in a short ADR describing the selected adapter or policy and its contract.

1. Programming language and package layout.
2. Runtime schema/validation library.
3. Canonical JSON encoding rules.
4. Secure hashing algorithm and digest representation.
5. Evidence ID derivation and collision handling.
6. Initial persistent repository adapter, if one is needed beyond memory.
7. Transaction/unit-of-work implementation.
8. Redaction engine and organization-specific policy configuration.
9. Personal-data handling and quarantine policy.
10. Service/resource identity source and alias behavior.
11. Reliability scoring policy by source/evidence type.
12. Repeated-log signature algorithm and time bucket.
13. Approximate-time and clock-skew policy.
14. Timeline coincidence threshold.
15. Snapshot compact-projection size and truncation limits.
16. Query filter and pagination limits exposed to Person 3.
17. Audit/observability adapter.
18. Performance targets and fixture batch sizes.

No ADR may change a shared contract silently. Breaking changes require a schema version update and producer/consumer contract-test changes.

---

## 11. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Unredacted content leaks during hashing or errors | Hash in memory, discard raw bytes immediately, sanitize exception paths, and test captured logs |
| Source payload differences spread through the code | Isolate them behind source classifiers and canonical candidates |
| Replay creates duplicate evidence | Deterministic IDs, repository uniqueness semantics, and replay/property tests |
| Aggregation hides important repetitions | Preserve count, time range, and all contributing provenance |
| Timestamp normalization creates false precision | Preserve uncertainty and unknown values; never invent exact event times |
| Timeline implies causation | Restrict deterministic relationships and prohibit `CAUSES` in normal processing |
| Database replacement changes behavior | Domain-owned repository contracts plus shared adapter test suite |
| Person 3 depends on storage internals | Expose only snapshots, evidence IDs, domain filters, and repository read ports |
| Contract changes block parallel work | Freeze v1.0, version breaking changes, and retain recorded fixtures |
| Non-deterministic concurrency changes output | Sort at every boundary and derive IDs from stable canonical material |

---

## 12. Person 2 Definition of Done

Person 2 is ready for integration when all of the following are true:

- Any valid `RawEvidenceBatch` produces schema-valid canonical `EvidenceRecord` objects.
- All six required source categories are covered by fixtures and classifiers.
- Reprocessing the same batch is idempotent.
- Repeated evidence aggregation retains complete provenance.
- Synthetic sensitive values are absent from stored evidence, snapshots, warnings, errors, and captured telemetry.
- Full evidence is retrievable by every compact snapshot evidence ID.
- Source coverage distinguishes `available`, `empty`, `not_queried`, and `unavailable`.
- Timeline output is chronological, deterministic, and reference-valid.
- No causal relationship is inferred from time proximity alone.
- Snapshot revisioning is deterministic.
- Person 3 can query evidence and consume context without importing a database library.
- A replacement repository passes the same contract suite without changing domain output.
- Recorded replay tests pass without live external systems.
- Documentation explains how to add a new source evidence type, repository adapter, identity resolver, and redaction rule.
- No Person 2 module contains LLM calls, hypothesis generation, evidence support/contradiction judgments, ranking, or remediation logic.

---

## 13. Suggested Phase Checkpoints

Use these checkpoints before starting the next implementation phase:

| Checkpoint | Reviewers | Required approval |
|---|---|---|
| Phase 0 contracts frozen | Persons 1, 2, and 3 | All affected owners |
| Phase 1 schemas ready | Persons 1 and 3 | Producer/consumer contract examples accepted |
| Phase 2 repository ports ready | Person 3 | Read interface meets citation lookup needs |
| Phase 4 security boundary ready | Team/security reviewer if available | Synthetic secret suite passes |
| Phase 6 evidence pipeline ready | Person 1 | Recorded collection outputs process successfully |
| Phase 8 snapshot ready | Person 3 | Consumer tests pass without storage knowledge |
| Phase 9 final handoff | All three people | Pairwise and replay tests pass |

Implementation should begin only after the Phase 0 checkpoint is completed.
