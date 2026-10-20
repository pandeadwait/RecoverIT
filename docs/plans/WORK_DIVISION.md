# Three-Person Work Division

## Incident Investigation Through Ranked Root-Cause Hypotheses

| Field | Value |
|---|---|
| Status | Ready for implementation planning |
| Version | 1.0 |
| Date | 2026-09-12 |
| Related document | ARCHITECTURE.md |
| Implementation boundary | Ends after ranked hypotheses are produced |

---

## 1. Scope

The team will implement only the incident-investigation portion of the architecture:

1. Identify missing information.
2. Collect relevant logs, metrics, source-code changes, deployment history, pipeline results, and configuration changes.
3. Construct a chronological incident timeline.
4. Generate several root-cause hypotheses.
5. Attach supporting and contradicting evidence to every hypothesis.
6. Rank the hypotheses.

The team will not implement:

- Recovery recommendations.
- Human approval workflows.
- Action execution.
- Rollback, restart, scaling, or pipeline retry.
- Post-action verification.
- Self-healing.
- A production deployment.
- A complex user interface.

The final system output is a RankedHypothesisSet. Producing that output successfully is the end of the current workflow.

---

## 2. Design Rule: Everything Must Be Replaceable

The work is divided around interfaces, not technology brands.

The implementation must not make domain logic depend directly on:

- A particular LLM.
- A particular database.
- A particular web framework.
- A particular log or monitoring system.
- A particular Git host.
- A particular CI/CD product.
- A particular orchestration library.
- A particular cloud provider.

Every external dependency is accessed through a small interface. A concrete implementation is an adapter. Replacing a database, LLM, or data source should require replacing an adapter and rerunning contract tests—not rewriting the investigation logic.

### 2.1 Required Abstractions

~~~text
IncidentRepository
EvidenceRepository
HypothesisRepository
WorkQueue
ReasoningProvider
LogSource
MetricSource
ChangeSource
DeploymentSource
PipelineSource
ConfigurationSource
Clock
IdentifierGenerator
~~~

Tests must use in-memory or recorded implementations of these interfaces.

### 2.2 Decisions That May Change Later

| Decision | Stable abstraction | Examples of replaceable choices |
|---|---|---|
| Programming language or framework | API and domain contracts | Any suitable implementation |
| Database | Repository interfaces | Relational, document, embedded, or in-memory |
| LLM | ReasoningProvider | Hosted model, local model, rules, or test double |
| Agent framework | Investigation state transitions | Custom code, graph library, or workflow engine |
| Log platform | LogSource | File, log database, cloud logging, fixtures |
| Metrics platform | MetricSource | Time-series database, cloud monitoring, fixtures |
| Git integration | ChangeSource | Local repository, hosting API, fixtures |
| CI/CD integration | PipelineSource | CI product API, simulator, fixtures |
| Deployment integration | DeploymentSource | Platform API, event store, fixtures |
| Configuration integration | ConfigurationSource | Git, configuration store, audit log, fixtures |
| Presentation | Application output DTO | API, CLI, dashboard, or notebook |

No person's module may import a vendor SDK outside its adapter package.

---

## 3. Team Split Summary

| Person | Workstream | Primary input | Primary output |
|---|---|---|---|
| Person 1 | Incident intake and multi-source collection | IncidentAlert and EvidenceQueryPlan | IncidentSeed, SourceCapabilityCatalog, RawEvidenceBatch |
| Person 2 | Evidence normalization, storage, and timeline | IncidentSeed and RawEvidenceBatch | IncidentContextSnapshot |
| Person 3 | Missing-information analysis, hypotheses, and ranking | IncidentSeed, SourceCapabilityCatalog, IncidentContextSnapshot | EvidenceQueryPlan and RankedHypothesisSet |

### 3.1 Why This Split

The work follows the data flow:

~~~text
Person 1                     Person 2                     Person 3
────────                     ────────                     ────────
Incident intake              Normalize evidence          Find missing information
Source adapters      →       Store provenance     →      Request useful evidence
Query execution              Build timeline              Generate hypotheses
Capability catalogue         Build context snapshot      Cite support/contradictions
                                                          Rank hypotheses
~~~

The work is functionally independent:

- Person 1 can use fixed EvidenceQueryPlan fixtures.
- Person 2 can use recorded RawEvidenceBatch fixtures.
- Person 3 can use prepared IncidentContextSnapshot fixtures.
- Integration requires only the shared contracts in this document.

### 3.2 Approximate Work Balance

| Work area | Person 1 | Person 2 | Person 3 |
|---|---:|---:|---:|
| Domain and interface design | Medium | High | High |
| Adapter or processing implementation | High | High | Medium |
| Algorithmic work | Medium | High | High |
| Test fixture creation | High | Medium | High |
| Integration responsibility | Medium | High | High |
| Expected overall effort | Approximately equal | Approximately equal | Approximately equal |

Exact effort will vary. Functional ownership is more important than forcing identical line counts.

---

## 4. Shared End-to-End Workflow

~~~text
1. An IncidentAlert is received.
2. Person 1 validates it and produces IncidentSeed.
3. Person 1 publishes SourceCapabilityCatalog.
4. Person 3 compares the incident and current context with available capabilities.
5. Person 3 produces MissingInformationAssessment and EvidenceQueryPlan.
6. Person 1 executes the queries and returns RawEvidenceBatch.
7. Person 2 normalizes, deduplicates, redacts, and stores the evidence.
8. Person 2 rebuilds the timeline and returns IncidentContextSnapshot.
9. Person 3 generates or revises hypotheses.
10. Steps 4 through 9 repeat within configured budgets.
11. Person 3 validates evidence links and ranks the hypotheses.
12. The system returns RankedHypothesisSet and stops.
~~~

The loop must also support a first pass with an empty IncidentContextSnapshot.

---

## 5. Shared Contract Rules

All exchanged objects must:

- Include schema_version.
- Use globally unique opaque identifiers.
- Use UTC timestamps in RFC 3339 format.
- Distinguish event_time from observed_at and collected_at.
- Preserve source provenance.
- Treat absent values as null or omitted according to the schema, never as empty invented data.
- Use enumerations for controlled fields.
- Reject unknown fields at trust boundaries unless a versioned extension field is explicitly defined.
- Be serializable to JSON.
- Avoid vendor-specific objects.
- Avoid secrets and raw credentials.
- Produce deterministic canonical JSON for hashing and replay.
- Return structured errors instead of unhandled exceptions.

### 5.1 Common Error Format

~~~json
{
  "schema_version": "1.0",
  "code": "SOURCE_UNAVAILABLE",
  "message": "The metrics source did not respond before the deadline.",
  "retryable": true,
  "source": "metrics",
  "details": {
    "query_id": "qry_123"
  }
}
~~~

### 5.2 Time Semantics

- event_time: When the operational event occurred.
- observed_at: When the source observed or recorded the event.
- collected_at: When this project collected it.
- received_at: When an alert entered this project.

No component may use array order as a substitute for a timestamp.

---

# Part A — Person 1

## 6. Person 1: Incident Intake and Multi-Source Collection

### 6.1 Objective

Build the boundary between the investigation system and operational data sources.

Person 1 receives alerts and evidence queries, discovers what each source can provide, collects data through replaceable adapters, and returns source-neutral raw evidence batches.

### 6.2 Owned Functional Requirements

Person 1 owns:

- Alert ingestion and validation.
- Incident deduplication key generation.
- Service and environment scope extraction.
- Source capability discovery.
- Log collection.
- Metric collection.
- Source-code and Git change collection.
- Deployment-history collection.
- Pipeline-result collection.
- Configuration-change collection.
- Query validation and limits.
- Source timeouts and structured errors.
- Collection-level provenance.
- Raw fixture and replay adapters.

### 6.3 Explicitly Not Owned

Person 1 does not:

- Decide which root cause is most likely.
- Normalize evidence into final canonical records.
- Build the chronological timeline.
- Decide whether evidence supports a hypothesis.
- Generate or rank hypotheses.
- Call an LLM directly.
- Implement remediation actions.
- Expose a generic shell command.

### 6.4 Input Contracts

#### IncidentAlert

~~~json
{
  "schema_version": "1.0",
  "external_alert_id": "alert-001",
  "service": "payment-api",
  "environment": "simulation",
  "severity": "critical",
  "detected_at": "2026-09-12T10:30:00Z",
  "message": "HTTP 500 rate exceeded threshold",
  "labels": {
    "region": "local"
  }
}
~~~

Validation rules:

- external_alert_id, service, environment, severity, detected_at, and message are required.
- detected_at must include timezone information.
- severity must be an allowed enumeration.
- labels contain strings only.
- Message and labels are untrusted data.
- Repeated external_alert_id values are idempotent.

#### EvidenceQueryPlan

Person 1 consumes the complete plan defined under Person 3. Each query must use a source-neutral query type and validated arguments.

Example:

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "plan_id": "plan_002",
  "queries": [
    {
      "query_id": "qry_101",
      "source_type": "logs",
      "question": "Which errors appeared immediately after deployment?",
      "parameters": {
        "service": "payment-api",
        "start_time": "2026-09-12T10:20:00Z",
        "end_time": "2026-09-12T10:35:00Z",
        "severity": ["error", "critical"],
        "limit": 200
      }
    }
  ]
}
~~~

### 6.5 Output Contracts

#### IncidentSeed

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "external_alert_id": "alert-001",
  "service": "payment-api",
  "environment": "simulation",
  "severity": "critical",
  "detected_at": "2026-09-12T10:30:00Z",
  "received_at": "2026-09-12T10:30:02Z",
  "summary": "HTTP 500 rate exceeded threshold",
  "labels": {
    "region": "local"
  }
}
~~~

#### SourceCapabilityCatalog

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "generated_at": "2026-09-12T10:30:03Z",
  "sources": [
    {
      "source_type": "logs",
      "available": true,
      "supported_query_fields": [
        "service",
        "start_time",
        "end_time",
        "severity",
        "pattern",
        "limit"
      ],
      "maximum_window_seconds": 86400,
      "maximum_items": 1000
    },
    {
      "source_type": "configuration",
      "available": true,
      "supported_query_fields": [
        "service",
        "start_time",
        "end_time",
        "keys"
      ],
      "maximum_window_seconds": 604800,
      "maximum_items": 200
    }
  ]
}
~~~

#### RawEvidenceBatch

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "plan_id": "plan_002",
  "batch_id": "batch_009",
  "collected_at": "2026-09-12T10:31:00Z",
  "results": [
    {
      "query_id": "qry_101",
      "source_type": "logs",
      "source_adapter": "configured-log-adapter",
      "source_status": "ok",
      "truncated": false,
      "records": [
        {
          "source_record_id": "log-998",
          "event_time": "2026-09-12T10:27:15Z",
          "observed_at": "2026-09-12T10:27:16Z",
          "content_type": "application_log",
          "payload": {
            "level": "error",
            "message": "Database connection timeout",
            "service": "payment-api"
          }
        }
      ],
      "warnings": []
    }
  ],
  "errors": []
}
~~~

### 6.6 Adapter Interfaces

~~~text
AlertIngestor.ingest(alert) -> IncidentSeed
SourceRegistry.capabilities(incident) -> SourceCapabilityCatalog
LogSource.query(query) -> SourceResult
MetricSource.query(query) -> SourceResult
ChangeSource.query(query) -> SourceResult
DeploymentSource.query(query) -> SourceResult
PipelineSource.query(query) -> SourceResult
ConfigurationSource.query(query) -> SourceResult
CollectionService.execute(plan) -> RawEvidenceBatch
~~~

Concrete adapters may use any technology. They must return the shared contracts.

### 6.7 Collection Rules

- Only execute query types present in SourceCapabilityCatalog.
- Apply per-source timeout, item, byte, and time-window limits.
- Never broaden the requested service or time scope silently.
- Mark partial and truncated results.
- Preserve original source IDs when available.
- Do not interpret whether a record supports a hypothesis.
- Do not discard malformed records without a warning.
- Run independent read-only queries concurrently when safe.
- Record collection duration and source status.
- Never include credentials in RawEvidenceBatch.

### 6.8 Required Deliverables

1. Alert-ingestion interface and validation.
2. IncidentSeed construction.
3. SourceCapabilityCatalog generation.
4. Six abstract source interfaces:
   - Logs.
   - Metrics.
   - Changes.
   - Deployments.
   - Pipelines.
   - Configuration.
5. At least one fixture or simulator adapter for each source.
6. CollectionService that executes EvidenceQueryPlan.
7. Timeouts, limits, and structured error handling.
8. Recorded-fixture adapter for offline tests.
9. Contract tests for every adapter.
10. Integration guide explaining how to add a new source.

### 6.9 Required Tests

- Valid and invalid alerts.
- Duplicate alert ingestion.
- Every adapter returning a successful result.
- Source unavailable.
- Partial and truncated results.
- Invalid query fields.
- Query outside allowed time range.
- Concurrent independent queries.
- Credential-like data not appearing in errors or logs.
- Replay fixture returning deterministic output.
- All outputs round-trip through JSON validation.

### 6.10 Acceptance Criteria

Person 1's work is complete when:

- A valid alert produces exactly one IncidentSeed.
- Available sources are described without vendor-specific types.
- A valid EvidenceQueryPlan produces a validated RawEvidenceBatch.
- All six evidence categories are represented.
- Source failures do not crash the collection service.
- No hypothesis or ranking logic exists inside collection code.
- Person 2 can consume the output using only the published schema.
- Person 3 can test against fixture outputs without a live data source.

---

# Part B — Person 2

## 7. Person 2: Evidence Normalization, Storage, and Timeline

### 7.1 Objective

Transform heterogeneous source records into safe, deduplicated, traceable evidence and construct the chronological incident context used for reasoning.

Person 2 owns the canonical data model that separates the collectors from the reasoning system.

### 7.2 Owned Functional Requirements

Person 2 owns:

- Canonical evidence schema.
- Evidence type classification.
- Timestamp normalization.
- Service and resource identity normalization.
- Secret and sensitive-data redaction.
- Evidence deduplication.
- Evidence reliability and freshness metadata.
- Provenance and raw-record hashing.
- Evidence persistence interfaces.
- Timeline-event construction.
- Temporal relationship construction.
- IncidentContextSnapshot assembly.
- Evidence lookup by immutable ID.
- Recorded context fixtures for independent testing.

### 7.3 Explicitly Not Owned

Person 2 does not:

- Connect directly to live logging, monitoring, Git, CI/CD, or configuration systems.
- Decide which source should be queried next.
- Generate root-cause hypotheses.
- Rank hypotheses.
- Decide that temporal proximity proves causation.
- Call an LLM directly.
- Implement recovery actions.

### 7.4 Input Contracts

Person 2 consumes:

- IncidentSeed.
- One or more RawEvidenceBatch objects.
- An optional previous IncidentContextSnapshot when updating an investigation.

The exact formats are defined under Person 1 and in the shared schemas.

### 7.5 Output Contracts

#### EvidenceRecord

~~~json
{
  "schema_version": "1.0",
  "evidence_id": "ev_201",
  "incident_id": "inc_001",
  "source_type": "logs",
  "evidence_type": "error_event",
  "service": "payment-api",
  "event_time": "2026-09-12T10:27:15Z",
  "observed_at": "2026-09-12T10:27:16Z",
  "collected_at": "2026-09-12T10:31:00Z",
  "summary": "Payment API logged a database connection timeout.",
  "attributes": {
    "level": "error",
    "error_signature": "database_connection_timeout"
  },
  "provenance": {
    "batch_id": "batch_009",
    "query_id": "qry_101",
    "source_record_id": "log-998",
    "source_adapter": "configured-log-adapter",
    "raw_payload_hash": "sha256:..."
  },
  "quality": {
    "reliability": "high",
    "freshness_seconds": 224,
    "truncated_source": false,
    "redactions_applied": false
  }
}
~~~

#### TimelineEvent

~~~json
{
  "schema_version": "1.0",
  "timeline_event_id": "tle_301",
  "incident_id": "inc_001",
  "event_time": "2026-09-12T10:27:15Z",
  "time_uncertainty_ms": 0,
  "category": "symptom",
  "title": "Database connection timeouts began",
  "service": "payment-api",
  "evidence_ids": ["ev_201"]
}
~~~

#### TemporalRelationship

~~~json
{
  "schema_version": "1.0",
  "relationship_id": "rel_401",
  "incident_id": "inc_001",
  "from_event_id": "tle_300",
  "to_event_id": "tle_301",
  "relationship_type": "PRECEDES",
  "delta_ms": 120000,
  "created_by": "deterministic"
}
~~~

#### IncidentContextSnapshot

~~~json
{
  "schema_version": "1.0",
  "snapshot_id": "ctx_501",
  "incident_id": "inc_001",
  "revision": 3,
  "created_at": "2026-09-12T10:31:02Z",
  "incident": {
    "service": "payment-api",
    "environment": "simulation",
    "severity": "critical",
    "detected_at": "2026-09-12T10:30:00Z",
    "summary": "HTTP 500 rate exceeded threshold"
  },
  "evidence": [
    {
      "evidence_id": "ev_201",
      "source_type": "logs",
      "evidence_type": "error_event",
      "event_time": "2026-09-12T10:27:15Z",
      "summary": "Payment API logged a database connection timeout.",
      "quality": {
        "reliability": "high",
        "freshness_seconds": 224
      }
    }
  ],
  "timeline": [
    {
      "timeline_event_id": "tle_301",
      "event_time": "2026-09-12T10:27:15Z",
      "category": "symptom",
      "title": "Database connection timeouts began",
      "evidence_ids": ["ev_201"]
    }
  ],
  "relationships": [],
  "source_coverage": {
    "logs": "available",
    "metrics": "not_queried",
    "changes": "not_queried",
    "deployments": "not_queried",
    "pipelines": "not_queried",
    "configuration": "not_queried"
  },
  "warnings": []
}
~~~

The snapshot may contain compact evidence projections, but EvidenceRepository must return the full EvidenceRecord by ID.

### 7.6 Repository Interfaces

~~~text
IncidentRepository.get(incident_id) -> IncidentSeed
EvidenceRepository.save_all(records) -> list[evidence_id]
EvidenceRepository.get(evidence_id) -> EvidenceRecord
EvidenceRepository.query(filter) -> list[EvidenceRecord]
TimelineRepository.replace_revision(incident_id, revision, events, relationships)
TimelineRepository.get_latest(incident_id) -> Timeline
ContextRepository.save(snapshot) -> snapshot_id
ContextRepository.get_latest(incident_id) -> IncidentContextSnapshot
~~~

No domain service may depend on database-specific query objects.

### 7.7 Normalization Pipeline

~~~text
RawEvidenceBatch
→ schema validation
→ source-record classification
→ timestamp normalization
→ canonical service mapping
→ secret redaction
→ deterministic deduplication
→ summary and feature extraction
→ provenance attachment
→ persistence
→ timeline event construction
→ relationship calculation
→ IncidentContextSnapshot
~~~

### 7.8 Deduplication Rules

Evidence may be considered duplicate when:

- It has the same source, source-record ID, and payload hash.
- A repeated log has the same normalized signature, service, and configured time bucket.
- A deployment or commit has the same stable external ID.
- The same batch is replayed.

Deduplication must never remove provenance. Repeated events may be represented by one evidence record with count and time-range attributes.

### 7.9 Timeline Rules

- Sort primarily by event_time.
- Preserve observed_at and collected_at separately.
- Use deterministic tie-breaking by evidence ID.
- Mark unknown event times rather than inventing them.
- Record time uncertainty when only approximate time is available.
- Calculate deltas using code, not the reasoning provider.
- Link changes to deployments only when a shared revision or deployment identifier exists.
- Label simple temporal relationships as PRECEDES or COINCIDES_WITH.
- Do not label CAUSES unless externally provided ground truth is being used only by evaluation.

### 7.10 Redaction Rules

At minimum detect and redact:

- API keys and bearer tokens.
- Passwords.
- Database connection strings.
- Private keys.
- Common cloud credentials.
- Secret-valued environment variables.
- Email or personal identifiers if configured.

Store a hash of the original payload, not the secret value. Tests must use synthetic secrets only.

### 7.11 Required Deliverables

1. Versioned EvidenceRecord schema.
2. Versioned TimelineEvent and TemporalRelationship schemas.
3. Repository interfaces with in-memory implementations.
4. Evidence-normalization pipeline.
5. Timestamp normalization.
6. Canonical service mapping.
7. Redaction layer.
8. Deduplication and repeated-event aggregation.
9. Provenance and raw-payload hashing.
10. Timeline builder.
11. IncidentContextSnapshot builder.
12. Evidence lookup and context-query service.
13. Recorded context fixtures.
14. Unit, contract, and integration tests.

### 7.12 Required Tests

- Every source category maps to canonical evidence.
- UTC conversion with different timezones.
- Missing and approximate event times.
- Stable ordering for equal timestamps.
- Duplicate-batch replay.
- Repeated-log aggregation.
- Synthetic secret redaction.
- Provenance preservation after redaction and deduplication.
- Timeline relationships based on real shared identifiers.
- No causal relationship inferred from timing alone.
- Repository replacement using the same contract suite.
- Snapshot revision increases deterministically.
- Full evidence can be retrieved from a compact snapshot reference.

### 7.13 Acceptance Criteria

Person 2's work is complete when:

- Any valid RawEvidenceBatch produces canonical EvidenceRecord objects.
- Reprocessing the same batch does not create duplicate evidence.
- Sensitive test values are removed before context output.
- The timeline is deterministic and chronological.
- Every timeline event references existing evidence.
- Source coverage distinguishes unavailable, not queried, empty, and available.
- Person 3 can use IncidentContextSnapshot without knowing the database.
- Changing the repository implementation does not change domain behavior.

---

# Part C — Person 3

## 8. Person 3: Missing-Information Analysis, Hypotheses, and Ranking

### 8.1 Objective

Build the bounded investigation intelligence that determines what is missing, requests relevant evidence, generates multiple hypotheses, links evidence, and produces the final ranking.

Person 3 owns the investigation workflow but accesses collection and storage only through shared interfaces.

### 8.2 Owned Functional Requirements

Person 3 owns:

- Investigation state machine.
- MissingInformationAssessment.
- EvidenceQueryPlan generation.
- Investigation budgets and stopping rules.
- ReasoningProvider abstraction.
- Structured-output schemas.
- Prompt or reasoning-strategy versioning.
- Hypothesis generation.
- Hypothesis revision.
- Support and contradiction assignment.
- Evidence citation validation.
- Deterministic ranking features.
- Final RankedHypothesisSet.
- Inconclusive and abstention behavior.
- Replay with fake or recorded reasoning providers.

### 8.3 Explicitly Not Owned

Person 3 does not:

- Implement vendor-specific data-source queries.
- Parse raw source formats.
- Store data using database-specific APIs.
- Infer timestamps from array order.
- Execute arbitrary commands.
- Recommend or perform remediation.
- Hide missing or contradictory evidence.
- Allow the reasoning provider to directly access infrastructure.

### 8.4 Input Contracts

Person 3 consumes:

- IncidentSeed.
- SourceCapabilityCatalog.
- Latest IncidentContextSnapshot.
- Configured InvestigationBudget.
- Results returned through Person 1 and Person 2 contracts.

#### InvestigationBudget

~~~json
{
  "schema_version": "1.0",
  "max_rounds": 6,
  "max_queries": 12,
  "max_elapsed_seconds": 900,
  "max_reasoning_calls": 10,
  "max_input_units": 100000,
  "max_output_units": 20000,
  "minimum_hypotheses": 2,
  "maximum_hypotheses": 5
}
~~~

Input and output units are provider-neutral accounting values. A model adapter may map them to tokens or another billing unit.

### 8.5 Intermediate Output Contracts

#### MissingInformationAssessment

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "assessment_id": "mia_601",
  "known_facts": [
    {
      "statement": "HTTP 500 errors exceeded the alert threshold.",
      "evidence_ids": ["ev_100"]
    }
  ],
  "missing_information": [
    {
      "information_id": "need_01",
      "question": "Was a deployment completed shortly before the error increase?",
      "reason": "This distinguishes a recent-change regression from an independent outage.",
      "priority": "high",
      "candidate_sources": ["deployments", "changes"],
      "resolved": false
    }
  ],
  "unavailable_information": [],
  "recommended_stop": false
}
~~~

#### EvidenceQueryPlan

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "plan_id": "plan_002",
  "round": 2,
  "queries": [
    {
      "query_id": "qry_101",
      "source_type": "logs",
      "question": "Which errors appeared immediately after deployment?",
      "parameters": {
        "service": "payment-api",
        "start_time": "2026-09-12T10:20:00Z",
        "end_time": "2026-09-12T10:35:00Z",
        "severity": ["error", "critical"],
        "limit": 200
      },
      "related_information_ids": ["need_02"],
      "discriminates_hypothesis_ids": ["hyp_01", "hyp_02"],
      "expected_information_value": "high"
    }
  ],
  "stop_reason": null
}
~~~

The plan can contain zero queries only when stop_reason is present.

#### Hypothesis

~~~json
{
  "schema_version": "1.0",
  "hypothesis_id": "hyp_01",
  "incident_id": "inc_001",
  "revision": 2,
  "statement": "The deployment introduced an invalid database connection configuration.",
  "root_cause_category": "configuration_regression",
  "affected_component": "payment-api",
  "supporting_evidence": [
    {
      "evidence_id": "ev_201",
      "reason": "Connection timeouts began after the new deployment."
    }
  ],
  "contradicting_evidence": [
    {
      "evidence_id": "ev_205",
      "reason": "One timeout occurred before the deployment."
    }
  ],
  "missing_information_ids": ["need_04"],
  "testable_prediction": "The configuration diff should contain a database endpoint change.",
  "status": "active"
}
~~~

### 8.6 Final Output Contract

#### RankedHypothesisSet

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "context_snapshot_id": "ctx_501",
  "ranking_id": "rank_701",
  "created_at": "2026-09-12T10:33:00Z",
  "status": "completed",
  "hypotheses": [
    {
      "rank": 1,
      "hypothesis_id": "hyp_01",
      "statement": "The deployment introduced an invalid database connection configuration.",
      "root_cause_category": "configuration_regression",
      "affected_component": "payment-api",
      "evidence_score": 84.0,
      "confidence_label": "high",
      "supporting_evidence": [
        {
          "evidence_id": "ev_201",
          "reason": "Database timeouts began after deployment."
        },
        {
          "evidence_id": "ev_203",
          "reason": "The configuration change modified the database endpoint."
        }
      ],
      "contradicting_evidence": [],
      "unresolved_questions": [],
      "score_breakdown": {
        "independent_source_support": 22.0,
        "symptom_coverage": 18.0,
        "temporal_consistency": 14.0,
        "change_consistency": 14.0,
        "specificity": 8.0,
        "prediction_support": 8.0,
        "contradiction_penalty": 0.0,
        "missing_evidence_penalty": 0.0
      }
    }
  ],
  "remaining_uncertainty": [
    "The exact runtime value of the database endpoint was not independently observed."
  ],
  "budget_usage": {
    "rounds": 3,
    "queries": 7,
    "reasoning_calls": 5
  }
}
~~~

If ranking cannot be supported:

~~~json
{
  "schema_version": "1.0",
  "incident_id": "inc_001",
  "context_snapshot_id": "ctx_501",
  "ranking_id": "rank_702",
  "created_at": "2026-09-12T10:33:00Z",
  "status": "inconclusive",
  "hypotheses": [],
  "remaining_uncertainty": [
    "Logs and deployment history were unavailable."
  ],
  "stop_reason": "insufficient_evidence",
  "budget_usage": {
    "rounds": 2,
    "queries": 4,
    "reasoning_calls": 3
  }
}
~~~

### 8.7 ReasoningProvider Interface

~~~text
assess_missing_information(
    incident,
    source_capabilities,
    context,
    active_hypotheses
) -> MissingInformationAssessment

plan_queries(
    missing_information,
    source_capabilities,
    context,
    budget
) -> EvidenceQueryPlan

generate_hypotheses(
    incident,
    context,
    limits
) -> HypothesisSet

revise_hypotheses(
    previous_hypotheses,
    new_context
) -> HypothesisSet
~~~

The provider returns structured domain objects. Provider-specific response objects must not escape its adapter.

Required implementations:

- At least one configured reasoning adapter.
- A deterministic fake provider for tests.
- A recorded-response provider for replay.

### 8.8 Investigation Loop

~~~text
Start with IncidentSeed and empty context
→ assess missing information
→ plan allowed queries
→ invoke collection abstraction
→ invoke context-building abstraction
→ generate or revise hypotheses
→ validate evidence citations
→ check stopping rules
→ repeat if useful information is still available
→ calculate ranking features
→ create RankedHypothesisSet
→ stop
~~~

### 8.9 Stopping Rules

Stop and rank when:

- Leading hypotheses have adequate evidence coverage.
- Requested high-value questions are resolved.
- Additional available queries have low expected value.
- The maximum investigation budget is reached.
- Required sources are unavailable and further collection cannot help.

Use status inconclusive when:

- Fewer than the minimum hypotheses can be responsibly generated.
- No hypothesis has valid supporting evidence.
- Critical evidence sources are unavailable.
- Structured output repeatedly fails validation.
- The score difference is too small and evidence is inadequate.

### 8.10 Citation Validation

Before ranking:

- Every evidence_id must exist in the supplied context or repository.
- The same citation cannot appear as both support and contradiction for one hypothesis without an explicit explanation.
- Evidence from another incident is rejected.
- Truncated or low-reliability evidence remains marked.
- A reason must explain how each citation affects the hypothesis.
- Uncited factual claims are rejected or labeled as assumptions.

### 8.11 Ranking Method

The ranking engine combines provider assessment with deterministic features.

Suggested features:

- Independent source support.
- Symptom coverage.
- Temporal consistency.
- Change consistency.
- Hypothesis specificity.
- Prediction support.
- Contradiction penalty.
- Missing critical evidence penalty.
- Low-reliability evidence penalty.

Requirements:

- Feature calculators are deterministic and individually testable.
- Weights are configuration, not hard-coded domain rules.
- Equal scores use deterministic tie-breaking.
- A confidence label is derived from configured thresholds.
- Evidence score is not described as a probability until calibrated.
- The model cannot directly assign the final numeric score.

### 8.12 Required Deliverables

1. Investigation state machine.
2. InvestigationBudget schema and enforcement.
3. MissingInformationAssessment schema and service.
4. EvidenceQueryPlan schema and query validator.
5. ReasoningProvider interface.
6. Configured, fake, and recorded provider adapters.
7. Structured hypothesis-generation and revision services.
8. Evidence-citation validator.
9. Ranking feature calculators.
10. Configurable ranking engine.
11. RankedHypothesisSet schema.
12. Inconclusive and abstention behavior.
13. Replay-capable investigation runner.
14. Unit, contract, integration, and scenario tests.

### 8.13 Required Tests

- Empty initial context identifies meaningful missing information.
- Queries use only advertised capabilities.
- Duplicate queries are rejected.
- Investigation budget is enforced.
- Multiple hypotheses are generated.
- Recent deployment is not automatically ranked first.
- Supporting and contradicting evidence remain separate.
- Invalid or cross-incident evidence IDs are rejected.
- Missing evidence applies a penalty.
- Ranking is deterministic for fixed inputs.
- Equal-score tie-breaking is stable.
- Provider replacement passes the same contract tests.
- Repeated invalid structured output becomes inconclusive.
- A full recorded run is replayable without a live model.
- Unavailable sources produce uncertainty rather than invented facts.

### 8.14 Acceptance Criteria

Person 3's work is complete when:

- The system explicitly reports missing information.
- Every requested query is valid against SourceCapabilityCatalog.
- At least two hypotheses are produced for suitable scenarios.
- Every hypothesis includes support, contradictions, or an explicit statement that none were found.
- Every citation resolves to evidence.
- Final numeric ranking is reproducible for identical inputs.
- The workflow stops at RankedHypothesisSet.
- No remediation recommendation or execution path exists.
- Replacing ReasoningProvider does not change orchestration contracts.

---

## 9. Shared Integration Contracts

### 9.1 Person 1 to Person 2

~~~text
IncidentSeed
RawEvidenceBatch
~~~

Person 2 must not depend on Person 1's adapter classes. Integration occurs through serialized, schema-validated objects.

### 9.2 Person 2 to Person 3

~~~text
IncidentContextSnapshot
EvidenceRepository read interface
~~~

Person 3 must not depend on a database library or Person 2's internal normalization classes.

### 9.3 Person 3 to Person 1

~~~text
EvidenceQueryPlan
~~~

Person 1 executes the plan but does not reinterpret its investigative purpose.

### 9.4 Person 3 Final Output

~~~text
RankedHypothesisSet
~~~

This is the project boundary and the final current deliverable.

### 9.5 Shared Schema Location

Place technology-neutral schemas in one shared package:

~~~text
contracts/
├── incident/
├── collection/
├── evidence/
├── timeline/
├── investigation/
├── hypothesis/
└── errors/
~~~

Changes to shared schemas require:

1. Schema-version update when compatibility changes.
2. Review by all affected owners.
3. Updated producer and consumer contract tests.
4. Updated example fixtures.
5. A short compatibility note.

---

## 10. Recommended Repository Ownership

~~~text
project/
├── contracts/                         # Shared; reviewed by all
├── ingestion/                         # Person 1
│   ├── alert/
│   ├── capabilities/
│   └── validation/
├── collectors/                        # Person 1
│   ├── gateway/
│   ├── logs/
│   ├── metrics/
│   ├── changes/
│   ├── deployments/
│   ├── pipelines/
│   ├── configuration/
│   └── fixtures/
├── evidence/                          # Person 2
│   ├── normalization/
│   ├── redaction/
│   ├── deduplication/
│   ├── provenance/
│   ├── repositories/
│   └── context/
├── timeline/                          # Person 2
│   ├── builder/
│   └── relationships/
├── investigation/                     # Person 3
│   ├── orchestration/
│   ├── missing_information/
│   ├── query_planning/
│   └── budgets/
├── reasoning/                         # Person 3
│   ├── provider/
│   ├── prompts_or_strategies/
│   ├── hypotheses/
│   └── ranking/
├── tests/
│   ├── contract/                      # Shared
│   ├── integration/                   # Shared
│   ├── scenarios/                     # Shared
│   └── fixtures/                      # Shared
└── docs/
~~~

The folder names are illustrative. Ownership boundaries and contracts are mandatory; language-specific packaging is not.

---

## 11. Parallel Development Plan

### Stage 1: Contract Freeze

All three people jointly review only:

- Shared identifiers.
- Timestamp rules.
- Enumerations.
- Error format.
- IncidentSeed.
- SourceCapabilityCatalog.
- EvidenceQueryPlan.
- RawEvidenceBatch.
- EvidenceRecord.
- IncidentContextSnapshot.
- RankedHypothesisSet.

After this review, version 1.0 contracts are frozen for the first integration.

### Stage 2: Independent Implementation

**Person 1** works with hand-written EvidenceQueryPlan fixtures.

**Person 2** works with recorded RawEvidenceBatch fixtures.

**Person 3** works with prepared IncidentContextSnapshot and SourceCapabilityCatalog fixtures.

No person should wait for another person's concrete module.

### Stage 3: Pairwise Integration

1. Person 1 output into Person 2 input.
2. Person 3 plan into Person 1 collection.
3. Person 2 context into Person 3 reasoning.

Each pair runs contract tests before full integration.

### Stage 4: End-to-End Integration

Run:

~~~text
IncidentAlert
→ IncidentSeed
→ MissingInformationAssessment
→ EvidenceQueryPlan
→ RawEvidenceBatch
→ IncidentContextSnapshot
→ HypothesisSet
→ RankedHypothesisSet
~~~

### Stage 5: Scenario Evaluation

Use at least these cases:

1. Deployment configuration regression.
2. Memory exhaustion.
3. Dependency incompatibility.
4. Actual database outage.
5. Coincidental deployment that is not the cause.

---

## 12. Shared Definition of Done

The combined work is complete when:

1. A valid alert starts an investigation.
2. The system reports what information is missing.
3. It queries all six required evidence categories when relevant.
4. All collected data passes through source-neutral contracts.
5. Evidence is normalized, redacted, deduplicated, and traceable.
6. A deterministic chronological timeline is constructed.
7. Several hypotheses are generated for suitable cases.
8. Every hypothesis references valid supporting and contradicting evidence.
9. Hypotheses receive deterministic ranks and score breakdowns.
10. The result clearly shows remaining uncertainty.
11. The workflow ends at RankedHypothesisSet.
12. No recovery or execution functionality is implemented.
13. LLM, database, framework, and source adapters can be replaced.
14. Unit, contract, integration, replay, and scenario tests pass.
15. A recorded end-to-end run works without live external services.

---

## 13. Final Responsibility Matrix

| Deliverable | Person 1 | Person 2 | Person 3 |
|---|---|---|---|
| IncidentAlert validation | Owner | Reviewer | Consumer |
| IncidentSeed | Owner | Consumer | Consumer |
| SourceCapabilityCatalog | Owner | Reviewer | Consumer |
| Source adapter interfaces | Owner | Reviewer | Consumer |
| RawEvidenceBatch | Owner | Consumer | Reviewer |
| EvidenceRecord | Producer input reviewer | Owner | Consumer |
| Redaction and deduplication | Contributor | Owner | Reviewer |
| EvidenceRepository | Reviewer | Owner | Consumer |
| TimelineEvent and relationships | Contributor | Owner | Consumer |
| IncidentContextSnapshot | Reviewer | Owner | Consumer |
| MissingInformationAssessment | Reviewer | Contributor | Owner |
| EvidenceQueryPlan | Consumer | Reviewer | Owner |
| ReasoningProvider | Reviewer | Reviewer | Owner |
| Hypothesis generation | Reviewer | Evidence reviewer | Owner |
| Citation validation | Reviewer | Repository support | Owner |
| Ranking engine | Reviewer | Feature reviewer | Owner |
| RankedHypothesisSet | Reviewer | Reviewer | Owner |
| Pairwise contract tests | Co-owner | Co-owner | Co-owner |
| End-to-end scenario tests | Co-owner | Co-owner | Co-owner |

Owner means responsible for implementation and final correctness. Consumer means the person's module uses the contract. Reviewer means the person verifies integration requirements.

