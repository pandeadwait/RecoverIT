# Phase 9 Integration, Hardening, and Person 2 Handoff

## Final scope

Phase 9 closes Person 2's implementation plan. The automated suite exercises
the complete contract path from serialized Person 1 inputs through canonical
evidence, security, deduplication, persistence, timeline construction, and
context publication. It then consumes the serialized snapshot and resolves its
evidence using only the Person 3 read interface.

The recorded replay catalogue is
`tests/fixtures/replay/phase_9_scenarios.json`. Every entry contains a complete
`IncidentSeed`, one complete `RawEvidenceBatch`, and reviewed expectations for
evidence volume, warning count, timeline order, relationships, source coverage,
revision, and deterministic snapshot ID.

## Replay and boundary results

| Recorded scenario | Evidence | Warnings | Snapshot bytes | Important assertion |
|---|---:|---:|---:|---|
| Deployment configuration regression | 3 | 0 | 3,431 | Shared revision produces `DEPLOYED_FROM` |
| Memory exhaustion | 2 | 1 | 2,339 | Synthetic API key is absent after redaction |
| Dependency incompatibility | 3 | 0 | 3,124 | Change, failed pipeline, and error remain observations |
| Database outage | 2 | 1 | 2,351 | Unavailable configuration is distinct from empty |
| Coincidental deployment | 3 | 0 | 3,088 | Coincidence produces no causal or revision link |

Measurements above use compact JSON with sorted keys. On the local reference
environment, five-run median end-to-end replay times were below 1 ms for each
small fixture. A separate 250-record repeated-log replay completed in about
150 ms and collapsed to one evidence record while retaining all 250 provenance
entries. Timing is informational and intentionally does not enter any domain
contract or deterministic ID.

The in-memory adapter is the selected Phase 9 adapter. Its reusable repository
contract suite covers save/get/query, filtering, pagination, stable ordering,
incident isolation, immutable conflicts, timeline revision replacement, context
latest lookup, and coordinator rollback. A future adapter must run the same
suite; no database-specific query type may enter an application service.

The final failure-path gate also covers malformed siblings, partial and
unavailable sources, cross-incident envelopes, conflicting batch IDs, storage
rollback, policy failure, private-key quarantine, audit-sink failure, invalid
cursors, stale references, and late context conflicts. Failures are either
isolated as sanitized warnings or translated to portable errors; adapter types
and sensitive exception content do not cross the boundary.

## Person 1 integration

Person 1 sends serialized version 1.0 contracts only:

```python
incident = IncidentSeed.from_dict(received_incident_json)
batch = RawEvidenceBatch.from_dict(received_batch_json)
result = processing_service.process(incident, (batch,))
```

Person 2 never imports a Person 1 collector or adapter. A malformed envelope is
rejected at contract parsing; an invalid record inside a valid batch is isolated
as a warning so valid sibling records can continue.

After evidence processing, construct and persist the timeline, then publish the
snapshot with the sanitized processing warnings:

```python
timeline_service.build_and_persist(incident.incident_id, records, revision=1)
snapshot = context_service.publish(
    incident,
    (batch,),
    warnings=result.warnings,
)
```

## Person 3 integration

Person 3 accepts the serialized `IncidentContextSnapshot` and the
`IncidentContextReader` interface only:

```python
snapshot = IncidentContextSnapshot.from_dict(received_snapshot_json)
latest = reader.get_latest_context(snapshot.incident_id)
full_record = reader.get_evidence(snapshot.evidence[0].evidence_id)
matching = reader.query_evidence(domain_filter)
```

The snapshot is compact; the reader resolves every projection to a complete
canonical record without exposing a database. Person 3 owns hypothesis
generation, evidence selection, support/contradiction judgments, ranking, and
the `ReasoningProvider` LLM adapter. Person 2 deliberately contains none of
those responsibilities.

## Extension procedures

### Add an evidence type

1. Implement `SourceClassifier` for the source contract, producing only a safe
   `ClassifiedRecord` with a stable evidence type, summary, selected attributes,
   and optional identities.
2. Register the classifier in `ClassifierRegistry`; do not add collector or SDK
   dependencies to Person 2.
3. Add serialized input fixtures and assertions for classification, timestamps,
   security, deterministic IDs, timeline category, replay, and context output.
4. Use an `x-` extension value when the v1 contract permits it. Otherwise make a
   versioned shared-contract change with both team-boundary tests.

### Add or change a redaction rule

1. Configure `PatternRedactionPolicy` for additional sensitive field names or
   personal identifiers, or provide another implementation of `RedactionPolicy`.
2. Keep redaction before evidence persistence and use synthetic secret values in
   tests.
3. Assert the value is absent from records, warnings, errors, audit events,
   snapshots, and exception text. Preserve only the raw-payload hash and safe
   redaction metadata.

### Add an identity source

1. Implement the two-method `IdentityResolver` port for service and resource
   aliases.
2. Return an explicit `IdentityResolution`; never silently guess an identity.
3. Test matched aliases, unknown fallbacks, deterministic output, and failure
   sanitization before injecting the resolver into `EvidenceNormalizationService`.

### Add a repository adapter

1. Implement `EvidenceRepository`, `TimelineRepository`, and
   `ContextRepository`, plus an atomic coordinator implementing the publication
   and evidence-batch coordinator ports.
2. Translate backend exceptions to portable repository errors and preserve
   immutable conflict semantics, incident isolation, stable ordering, and cursor
   behavior.
3. Mix `tests.repository_contract.RepositoryContract` into the adapter test class
   and run the entire Person 2 suite.
4. Compare fixed replay snapshots with the checked-in snapshot IDs. Adapter
   replacement must not change any serialized domain output.

## Handoff checklist

- [x] All six source categories normalize into canonical evidence.
- [x] Timezone, missing-time, approximation, and deterministic ordering tests pass.
- [x] Replay is idempotent and input permutations preserve outputs.
- [x] Repeated-log aggregation preserves every provenance entry.
- [x] Synthetic secrets are absent from Person 2 outputs and telemetry.
- [x] Every compact snapshot reference resolves to stored full evidence.
- [x] All four source-coverage states are distinct.
- [x] Timeline and snapshot references are incident-safe and complete.
- [x] Temporal proximity never emits `CAUSES`, support, or contradiction.
- [x] Snapshot revisions and IDs are deterministic under fixed inputs.
- [x] Person 1 and Person 3 pairwise tests use serialized contracts only.
- [x] Repository behavior is covered by an adapter-independent suite.
- [x] Production-source guard finds no LLM or hypothesis/ranking dependency.

Run the final gate from the repository root with:

```text
python3 -m unittest discover -s tests -v
python3 -m compileall -q -x '(^|/)\._' contracts evidence timeline tests
python3 -m pip check
git diff --check
```
