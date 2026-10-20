# Autonomous CI/CD Incident Triager & Self-Healer

## Software Architecture Document

| Field | Value |
|---|---|
| Status | Reference architecture; current implementation ends at ranked hypotheses |
| Version | 1.1 |
| Date | 2026-09-12 |
| Audience | Developers, evaluators, operators, and project reviewers |
| Source brief | Autonomous_CICD_Incident_Triager_Self_Healer (1).md |

---

## Contents

1. Purpose
2. Executive Summary
3. Goals and Non-Goals
4. Architecture Principles
5. Architecture Decisions
6. System Context
7. Container Architecture
8. Component Specifications
9. Investigation State Machine
10. Key Runtime Sequences
11. Data Architecture
12. HTTP API
13. Tool Contracts
14. LLM Reasoning Design
15. Security and Safety
16. Reliability and Failure Handling
17. Observability
18. Deployment Architecture
19. Suggested Repository Structure
20. Testing Strategy
21. Evaluation Design
22. Performance Assumptions
23. Innovation in the Architecture
24. Implementation Phases
25. Required Architecture Decision Records
26. Major Risks
27. Definition of Done
28. Immediate Implementation Milestone

---

## 1. Purpose

This document defines the architecture for an AI-assisted DevOps system. **The current implementation scope ends when the system produces a ranked set of root-cause hypotheses with supporting and contradicting evidence.** Recovery planning, action execution, approval, and post-action verification are retained only as possible future architecture and are not current deliverables.

It defines:

- System scope and trust boundaries.
- Services and internal components.
- Agent workflow and state transitions.
- LLM integration and model routing.
- Incident, evidence, timeline, hypothesis, ranking, and audit data.
- Tool and API contracts.
- Safety controls for read-only investigation.
- Deployment, observability, and failure handling.
- Testing and evaluation architecture.
- Implementation phases and acceptance criteria.

The first implementation is a controlled research prototype. It operates against a reproducible Docker-based environment rather than real production infrastructure.

---

## 2. Executive Summary

An alert enters the system and creates an incident. A single orchestrator runs a bounded investigation loop. It identifies missing information, uses a replaceable reasoning provider to form hypotheses and select useful evidence, and obtains operational data through typed, read-only tools.

Tool results are normalized, redacted, timestamped, assigned provenance, and stored before being supplied to the model. The agent must cite stored evidence when supporting or contradicting a hypothesis.

Tool results are normalized into a chronological timeline. The system generates several hypotheses, links support and contradictions to stored evidence, and ranks the hypotheses. It then stops and returns the ranked result for human review.

Every state transition, reasoning call, tool query, evidence record, and ranking revision is recorded for audit and experiment replay.

### 2.1 Plain-Language Glossary

| Term | Meaning in this project |
|---|---|
| Adapter | A small translator between our system and a source such as Git, Prometheus, or a pipeline |
| Agent | The investigation loop that observes, reasons, chooses tools, and updates its plan |
| API | A defined way for programs or the dashboard to send and receive data |
| Audit event | A permanent record of something the system or a user did |
| Evidence | A stored observation from logs, metrics, Git, deployments, health checks, or an operator |
| Evidence provenance | Where evidence came from, when it was obtained, and how it was processed |
| Evidence score | A ranking score based on support, contradictions, timeline, and symptom coverage; initially not a probability |
| Hypothesis | A possible explanation for the incident |
| Idempotency | Protection that prevents a retried request from performing the same action twice |
| Incident | The complete record of one detected failure and its investigation |
| LLM gateway | The controlled interface through which the application calls a language model |
| Orchestrator | The component that moves an incident through the investigation workflow |
| Policy engine | Non-AI rules that decide whether an action is denied, needs approval, or may run |
| Simulator | The isolated demo environment in which known failures are deliberately created |
| State machine | The allowed stages of an incident and the permitted transitions between them |
| Structured output | Model output required to match a predefined data schema rather than arbitrary prose |
| Tool | A narrowly defined function through which the agent reads data or requests an action |
| Verification | Fresh checks that determine whether an action actually improved the service |

---

## 3. Goals and Non-Goals

### 3.1 Goals

The current prototype must:

1. Receive simulated alerts through an HTTP API.
2. Collect logs, metrics, Git changes, deployment history, pipeline results, and service health.
3. Build a normalized incident timeline.
4. Generate multiple plausible root-cause hypotheses.
5. Attach supporting and contradicting evidence to each hypothesis.
6. Rank hypotheses using provider-independent reasoning and deterministic evidence features.
7. Return an evidence-linked investigation result for human review.
8. Replay recorded runs for testing.
9. Measure diagnostic accuracy, evidence quality, latency, reliability, and cost.

### 3.2 Non-Goals for the MVP

The MVP will not:

- Connect to real production infrastructure.
- Execute arbitrary model-generated shell, SQL, or Kubernetes commands.
- Perform database mutations or resource deletion.
- Recommend, approve, execute, or verify remediation actions.
- Implement a self-healing loop in the current phase.
- Use a multi-agent architecture by default.
- Fine-tune an LLM.
- Require a vector or graph database.
- Replace an incident commander.
- Claim causal proof from temporal proximity alone.
- Automatically modify source code.
- Perform organization-wide anomaly detection.

### 3.3 Future Scope

Possible later additions include remediation planning, approval, action execution, verification, Kubernetes adapters, real CI/CD connectors, incident-memory retrieval, service dependency graphs, and parallel specialist agents if evaluation demonstrates a benefit.

---

## 4. Architecture Principles

### 4.1 Evidence before conclusion

Important conclusions must cite stored evidence. Missing information must remain visible. The system may abstain or escalate rather than invent a fact.

### 4.2 Reasoning providers are advisory

The reasoning provider may propose queries and hypotheses. Application code validates schemas, calculates time and metric features, enforces budgets, and verifies that every evidence citation exists.

### 4.3 Read-only access

All tools in the current scope are read-only. No remediation or generic shell tool is exposed.

### 4.4 Durable and replayable state

The system checkpoints after every step. A worker crash must not erase progress. Recorded runs must be replayable without live infrastructure or model calls.

### 4.5 Bounded investigation

Each incident has limits for investigation rounds, tool calls, elapsed time, reasoning tokens, and estimated cost.

### 4.6 Provider independence

Domain interfaces must not depend directly on one model SDK, log platform, monitoring vendor, or CI/CD system.

### 4.7 Simulation first

Autonomous behavior is developed and evaluated in an isolated environment with deterministic resets and known ground truth.

### 4.8 Untrusted operational content

Alerts, logs, commit messages, code comments, and pipeline output are data, never instructions.

---

## 5. Architecture Decisions

The architecture fixes contracts and responsibilities, not product selections. Every infrastructure or AI dependency sits behind an interface so it can be replaced without changing the domain workflow.

| Decision area | Architectural requirement | Replaceable implementations |
|---|---|---|
| Runtime | Components communicate through documented interfaces | Any suitable language, web framework, or process model |
| Orchestration | A bounded workflow implements the defined investigation states | Custom state machine, workflow library, or durable workflow engine |
| Reasoning | A ReasoningProvider returns schema-validated results | Any hosted LLM, local model, rules engine, or test double |
| Persistence | Repository interfaces store incidents, evidence, timelines, and hypotheses | Relational, document, embedded, or in-memory database |
| Background processing | A WorkQueue claims idempotent investigation steps | Database queue, message broker, workflow service, or synchronous test runner |
| Logs | LogSource exposes the common evidence-query contract | Files, Loki, Elasticsearch, cloud logging, or fixtures |
| Metrics | MetricSource exposes allowlisted metric queries | Prometheus, cloud monitoring, time-series database, or fixtures |
| Source control | ChangeSource returns normalized changes | Git CLI, hosting API, or recorded fixtures |
| Deployments | DeploymentSource returns normalized deployment events | CI/CD API, platform API, event store, or fixtures |
| Pipelines | PipelineSource returns normalized run and stage results | Any CI/CD product or simulator |
| Configuration | ConfigurationSource returns versioned configuration changes | Git, configuration service, audit events, or fixtures |
| Presentation | Consumers read stable application DTOs | REST API, CLI, dashboard, notebook, or test harness |

Concrete products may be selected during implementation, but they are configuration or adapters—not domain dependencies. A replacement should require a new adapter and contract tests, not changes to incident, evidence, timeline, hypothesis, or ranking logic.

---

## 6. System Context

~~~mermaid
flowchart LR
    Engineer[Engineer or Evaluator]
    Alert[Simulated Alert Source]
    Git[Git Repository]
    Pipeline[CI/CD Simulator]
    Runtime[Demo Application]
    Metrics[Metrics Source]
    Logs[Log Store]
    Model[LLM Provider]
    System[Incident Triager and Self-Healer]

    Alert -->|incident| System
    Engineer -->|inspect, approve, configure| System
    System -->|status and report| Engineer
    System -->|read commits and diffs| Git
    System -->|read runs or retry| Pipeline
    System -->|health and allowlisted actions| Runtime
    System -->|queries| Metrics
    System -->|queries| Logs
    System -->|structured requests| Model
~~~

### 6.1 Actors

**Engineer:** Reviews incidents, evidence, hypotheses, and proposed actions. Approves or rejects gated actions.

**Evaluator:** Creates scenarios, resets the lab, runs experiments, and compares results with hidden ground truth.

**Administrator:** Configures users, policies, model credentials, and integration credentials.

**Alert source:** Sends an incident when scenario thresholds are exceeded.

### 6.2 Trust Boundaries

1. Alerts and operational data enter as untrusted input.
2. Model output is untrusted until schema and policy validation succeed.
3. Action execution crosses into a privileged boundary.
4. Approval requires authenticated user identity and authorization.
5. Evidence sent to an external model crosses a data-governance boundary.
6. Evaluation ground truth must remain inaccessible to the agent.

---

## 7. Container Architecture

~~~mermaid
flowchart TB
    Dashboard[Presentation Layer]
    API[Control API]
    Worker[Incident Worker]
    Orchestrator[Investigation Orchestrator]
    Context[Context Assembler]
    LLM[LLM Gateway]
    ToolGateway[Tool Gateway]
    Normalizer[Normalizer and Redactor]
    Timeline[Timeline Builder]
    Hypotheses[Hypothesis Engine]
    Scorer[Ranking Engine]
    Planner[Recovery Planner]
    Policy[Policy Engine]
    Approval[Approval Service]
    Executor[Action Executor]
    Verifier[Verification Engine]
    Reporter[Report Generator]
    Store[(Persistent Store)]
    Adapters[Source Adapters]
    Lab[Simulation Lab]

    Dashboard <--> API
    API --> Store
    API --> Worker
    Worker --> Orchestrator
    Orchestrator --> Context
    Context --> Store
    Orchestrator --> LLM
    Orchestrator --> ToolGateway
    ToolGateway --> Adapters
    Adapters --> Lab
    ToolGateway --> Normalizer
    Normalizer --> Timeline
    Timeline --> Store
    Orchestrator --> Hypotheses
    Hypotheses --> Scorer
    Orchestrator --> Planner
    Planner --> Policy
    Policy --> Approval
    Policy --> Executor
    Approval --> Executor
    Executor --> Lab
    Executor --> Verifier
    Verifier --> ToolGateway
    Orchestrator --> Reporter
    Reporter --> Store
~~~

The API, worker, and control-plane components may initially share one deployable codebase. Their interfaces remain separate so the programming language, framework, persistence mechanism, and process layout can change independently.

---

## 8. Component Specifications

### 8.1 Control API

**Purpose:** External HTTP interface for alerts, the dashboard, operators, and evaluation scripts.

**Responsibilities:**

- Validate incoming requests.
- Authenticate users and service clients.
- Enforce role-based authorization.
- Create incidents idempotently.
- Expose state, timeline, evidence, hypotheses, rankings, and investigation summaries.
- Accept cancellations and operator notes.
- Start controlled scenario and evaluation jobs.
- Return a correlation ID with every response.
- Create an audit event for every mutation.

**Important rules:**

- Alert ingestion requires a stable external alert ID or idempotency key.
- Duplicate alerts inside a configurable grouping window can attach to an existing incident.
- API responses never expose provider credentials or unredacted secrets.
- The API does not contain reasoning logic and cannot directly execute remediation.

### 8.2 Incident Manager

**Purpose:** Own the durable incident lifecycle.

**Responsibilities:**

- Create and group incidents.
- Track severity and affected service.
- Apply legal state transitions.
- Schedule investigation steps.
- Manage work leases.
- Detect stuck work.
- Support cancellation and escalation.

**Concurrency:**

- Every incident has a monotonically increasing state version.
- Updates use optimistic locking.
- A worker holds a short lease while processing a step.
- Recovery operations require idempotency keys and target locks.

### 8.3 Investigation Orchestrator

**Purpose:** Run the bounded investigation state machine.

**Responsibilities:**

- Select the next legal step.
- Assemble the minimum context required.
- Invoke reasoning components.
- Dispatch read-only tools.
- Persist state before and after external calls.
- Enforce investigation and cost budgets.
- Stop when evidence is sufficient, limits are reached, or human help is required.
- End with a ranked result or an explicit inconclusive outcome.

**Proposed initial limits:**

- Six evidence-gathering rounds.
- Twelve total investigation tool calls.
- At most two materially identical queries to the same tool.
- Fifteen minutes of elapsed investigation time.

These are configurable experimental defaults, not fixed product limits.

### 8.4 Context Assembler

**Purpose:** Build compact, structured input for each model decision.

**Responsibilities:**

- Load incident scope and current hypotheses.
- Select evidence by time, service, source, and relevance.
- Include support and contradictions.
- Replace large raw bodies with excerpts and evidence IDs.
- Track evidence already shown to the model.
- Separate system instructions from untrusted evidence.
- Estimate request tokens.

**Context order:**

1. Stable system rules and output schema.
2. Incident identity, scope, and remaining budgets.
3. Service and dependency metadata.
4. Normalized timeline.
5. Current hypotheses and citations.
6. New evidence.
7. Available tools or requested decision.

### 8.5 LLM Gateway

**Purpose:** Isolate model-provider details from the domain.

**Conceptual interface:**

~~~python
class ReasoningProvider(Protocol):
    async def generate_hypotheses(
        self, request: HypothesisRequest
    ) -> HypothesisSet: ...

    async def select_evidence(
        self, request: EvidenceSelectionRequest
    ) -> EvidenceQueryPlan: ...

    async def propose_action(
        self, request: ActionPlanningRequest
    ) -> ActionProposal: ...

    async def draft_report(
        self, request: ReportRequest
    ) -> ReportDraft: ...
~~~

**Responsibilities:**

- Translate domain objects into provider requests.
- Require versioned structured-output schemas.
- Apply model, reasoning, timeout, and token settings.
- Retry transient failures with bounded backoff.
- Record model, latency, token usage, and estimated cost.
- Support fake and recorded providers for testing.
- Avoid storing credentials or hidden reasoning content.
- Make provider replacement possible.

**Provider-neutral routing:**

- Hypothesis generation and difficult planning use the configured primary reasoning profile.
- Extraction and compression may use a configured economical reasoning profile.
- Quality-ceiling experiments may use a separate benchmark profile.
- Timeline calculations, citation validation, and final numeric scoring use deterministic code.

If the provider remains unavailable or produces invalid output after allowed retries, the incident escalates with all evidence preserved. An evaluation run must not silently switch models.

### 8.6 Evidence Query Planner

**Purpose:** Select the evidence most likely to reduce uncertainty.

**Inputs:**

- Active hypotheses.
- Missing facts.
- Available tools.
- Previous queries.
- Remaining budget.
- Source availability.

**Each proposed query includes:**

- Tool name and typed arguments.
- Question it should answer.
- Hypotheses it could strengthen or weaken.
- Expected information value.
- Estimated cost.

**Selection rules:**

1. Reject duplicates and invalid parameters.
2. Prefer queries that distinguish the leading hypotheses.
3. Prefer independent evidence sources.
4. Prefer narrow time and service scopes.
5. Run independent read-only queries concurrently.
6. Stop when expected information value is too low.

### 8.7 Tool Gateway

**Purpose:** Controlled access to operational sources.

**Responsibilities:**

- Maintain the server-owned tool registry.
- Validate parameters and permissions.
- Enforce time-range and result-size limits.
- Apply timeouts and circuit breakers.
- Attach tool version and invocation metadata.
- Return typed results rather than raw exceptions.
- Store query details and result references.
- Prevent writes through investigation tools.

**Common result envelope:**

~~~json
{
  "tool_call_id": "tc_123",
  "tool_name": "get_logs",
  "tool_version": "1.0",
  "requested_at": "2026-09-12T10:00:00Z",
  "completed_at": "2026-09-12T10:00:01Z",
  "source_status": "ok",
  "freshness_seconds": 4,
  "truncated": false,
  "items": [],
  "warnings": []
}
~~~

### 8.8 Log Adapter

- Query by service, time, severity, correlation ID, and safe text pattern.
- Limit line count and response bytes.
- Preserve event timestamps.
- Group repeated messages by normalized signature.
- Return representative excerpts and counts.
- Mark malformed records rather than silently dropping them.

### 8.9 Metrics Adapter

- Support allowlisted metric names and query templates.
- Return values, units, sampling intervals, and missing-data markers.
- Calculate baseline, peak, slope, and percentage change deterministically.
- Reject unrestricted model-generated query language in the MVP.

### 8.10 Git Adapter

- Return commits, changed files, selected diffs, and metadata.
- Restrict access to configured repositories and refs.
- Redact secrets detected in diffs.
- Treat commit messages and comments as untrusted input.
- Limit diff size and identify omitted or binary content.

### 8.11 Deployment Adapter

- Return deployment ID, artifact version, commit SHA, configuration fingerprint, timestamps, status, and previous healthy version.
- Connect deployments to pipeline runs and source revisions.
- Record rollback lineage.

### 8.12 CI/CD Adapter

- Return pipeline runs, stages, test results, artifacts, and errors.
- Keep retry capability in the separate Action Executor.
- Distinguish pipeline success from application health.

### 8.13 Health Adapter

- Return readiness, liveness, replica state, dependencies, and current version.
- Label stale or unavailable signals.
- Provide fresh observations for remediation verification.

### 8.14 Evidence Normalizer and Redactor

**Purpose:** Convert source-specific data into safe, comparable evidence.

**Responsibilities:**

- Normalize timestamps to UTC while preserving original timezone.
- Map services and resources to canonical IDs.
- Classify source and evidence type.
- Deduplicate observations.
- Redact tokens, credentials, connection strings, and personal data.
- Preserve a hash of the raw source record.
- Assign immutable evidence IDs.
- Record freshness, reliability, and truncation.
- Quarantine content that violates policy.

Redaction happens before evidence is sent to any model.

### 8.15 Timeline Builder

**Purpose:** Build an ordered operational history.

**Responsibilities:**

- Merge events from sources with different clocks.
- Preserve event time and observation time.
- Calculate time differences deterministically.
- Identify deployment, configuration, metric, log, alert, action, and verification milestones.
- Flag clock uncertainty and missing intervals.
- Create typed relationships such as PRECEDES, COINCIDES_WITH, DEPLOYED_FROM, SUPPORTS, and CONTRADICTS.

Temporal proximity increases relevance but never proves causation.

### 8.16 Evidence Store

**Purpose:** Persist the complete investigation.

For the current scope it stores:

- Incidents and workflow state.
- Source queries and tool results.
- Normalized evidence.
- Timeline events and relationships.
- Hypothesis revisions.
- Model-call metadata.
- Ranked hypothesis sets.
- Investigation summaries and audit events.

Large raw payloads can later move to object storage. The MVP may store compressed payloads through the configured repository implementation when they remain within configured limits.

### 8.17 Hypothesis Engine

**Purpose:** Generate and revise possible root causes.

A hypothesis contains:

- A clear causal statement.
- Affected component.
- Suspected change or failure.
- Supporting evidence IDs.
- Contradicting evidence IDs.
- Missing evidence.
- A testable prediction.
- Status: active, weakened, rejected, or selected.
- Evidence score and rank.

**Rules:**

- Generate at least two plausible hypotheses when possible.
- Include a cause unrelated to recent changes when evidence permits.
- Reject citations that do not exist in the evidence store.
- Never interpret unavailable evidence as support.
- Preserve rejected hypotheses for audit.

### 8.18 Ranking and Confidence Engine

**Purpose:** Rank hypotheses without presenting arbitrary model percentages as calibrated probability.

The MVP exposes an evidence score from 0 to 100 and a low, medium, or high label. It is called an evidence score until calibration demonstrates probabilistic meaning.

Proposed features:

- Independent supporting sources.
- Directness of evidence.
- Contradiction strength.
- Temporal consistency.
- Service-topology consistency.
- Coverage of observed symptoms.
- Availability of expected evidence.
- Hypothesis specificity.
- Accuracy of testable predictions.

Initial conceptual formula:

~~~text
evidence score =
    source support
  + symptom coverage
  + temporal consistency
  + topology consistency
  + prediction quality
  + specificity
  - contradiction penalty
  - missing-critical-evidence penalty
~~~

Weights will be developed using the training/development scenario set and frozen before final evaluation.

### 8.19 Recovery Planner

> **Future reference only:** Sections 8.19 through 8.24 describe the original full-system direction. They are explicitly outside the current implementation scope, which ends with Section 8.18, Ranking and Confidence Engine.

**Purpose:** Convert the selected hypothesis into a constrained action proposal.

The proposal contains:

- Action type and typed parameters.
- Selected hypothesis ID.
- Supporting evidence IDs.
- Expected benefit.
- Risk classification.
- Preconditions.
- Predicted post-action signals.
- Verification window.
- Failure or rollback strategy.
- Approval recommendation.

The planner cannot execute its proposal.

### 8.20 Policy Engine

**Purpose:** Make the authoritative authorization decision.

**Inputs:**

- Action and target.
- Environment.
- Incident severity.
- Evidence score.
- Autonomy mode.
- Previous attempts.
- Current policy version.
- Operational constraints.

**Outputs:**

- DENY
- REQUIRE_APPROVAL
- ALLOW

The decision includes matching rule IDs and an explanation.

**Initial rules:**

- Deny unknown action types or fields.
- Deny execution outside the simulator.
- Deny any raw command or script.
- Require approval below the high evidence threshold.
- Require approval for a second action.
- Allow one simulator restart when the service is unhealthy.
- Allow rollback only to a recorded previously healthy version.
- Deny scaling outside configured limits.
- Deny all database mutations.

Policies are versioned, and each decision records the exact version.

### 8.21 Approval Service

**Purpose:** Manage human authorization.

**Responsibilities:**

- Show action, evidence, risk, preconditions, and prediction.
- Accept approval or rejection from authorized users.
- Record identity, timestamp, comment, and policy version.
- Expire approvals after a configurable interval.
- Bind approval to a cryptographic hash of the exact proposal.
- Invalidate approval when the proposal changes.
- Prevent an approved action from being broadened.

### 8.22 Action Executor

**Purpose:** Run allowed actions through trusted adapters.

**Responsibilities:**

- Re-evaluate policy immediately before execution.
- Recheck target and preconditions.
- Use an idempotency key.
- Acquire a target-specific lock.
- Invoke the typed action adapter.
- Record start, completion, result, and side effects.
- Enforce timeout and retry policy.
- Use a compensating action only when predefined and safe.

There is no generic run-shell function.

### 8.23 Verification Engine

**Purpose:** Determine whether the incident improved.

**Process:**

1. Store a pre-action health and metric snapshot.
2. Wait for the action-specific stabilization interval.
3. Obtain fresh health, metric, log, and deployment evidence.
4. Evaluate deterministic success criteria.
5. Compare observations with the recorded prediction.
6. Return SUCCEEDED, PARTIAL, FAILED, or INCONCLUSIVE.

**Example rollback criteria:**

- Running version equals the approved target.
- Minimum ready-replica count is met.
- HTTP 500 rate remains below 2% for three samples.
- Restart rate returns to baseline.
- The relevant error signature stops increasing.

Execution success alone cannot resolve an incident.

### 8.24 Incident Report Generator

**Purpose:** Create machine-readable and human-readable reports.

A report contains:

- Summary and impact.
- Detection, diagnosis, action, and resolution times.
- Timeline.
- Hypotheses considered.
- Selected root cause and evidence score.
- Supporting and contradicting evidence.
- Recommended and executed actions.
- Policy and approval decisions.
- Verification criteria and results.
- Remaining uncertainty.
- Follow-up recommendations.
- Evidence references.

Reports are rendered from validated records. LLM-written prose cannot introduce uncited facts.

### 8.25 Incident Memory

The MVP stores previous incidents but does not use semantic retrieval in the primary benchmark.

A later version may retrieve structurally similar incidents using service, symptoms, error signatures, changed files, and root-cause category. Retrieved incidents remain advisory and cannot authorize actions.

Evaluation must prevent a test case from retrieving its own answer or a near-duplicate.

### 8.26 Dashboard

Primary views:

1. Incident list.
2. Incident overview and budget usage.
3. Investigation timeline.
4. Evidence explorer.
5. Hypothesis comparison.
6. Final ranked-hypothesis result.
7. Scenario and evaluation results.

The UI must visibly distinguish observed facts, model interpretations, operator statements, and hidden evaluation ground truth.

### 8.27 Audit Service

Every audit event contains:

- Event ID and incident ID.
- Event type and schema version.
- Actor type and ID.
- UTC timestamp.
- Correlation and causation IDs.
- Sanitized payload.
- Previous-event hash.
- Current-event hash.

Hash chaining makes accidental or simple retrospective alteration detectable. It does not replace proper database access control.

### 8.28 Scenario Controller and Fault Injector

**Responsibilities:**

- Reset the lab to a healthy known state.
- Seed data and start a selected application version.
- Inject a named fault using a deterministic seed.
- Emit an alert when thresholds are crossed.
- Store ground truth separately from agent-visible data.
- Restore the environment after a run.
- Vary timestamps, error wording, identifiers, and distractors.

The agent database role must not access ground truth.

### 8.29 Evaluation Harness

**Responsibilities:**

- Select scenarios and seeds.
- Configure model, prompts, budgets, and architecture variant.
- Reset and validate the environment.
- Run incidents to a terminal state.
- Compare outputs with ground truth.
- Aggregate accuracy, safety, latency, reliability, and cost.
- Repeat stochastic runs.
- Export raw results for analysis.

---

## 9. Investigation State Machine

~~~mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> ASSESSING_GAPS
    ASSESSING_GAPS --> COLLECTING_EVIDENCE: missing information identified
    COLLECTING_EVIDENCE --> BUILDING_TIMELINE: evidence batch available
    BUILDING_TIMELINE --> GENERATING_HYPOTHESES
    GENERATING_HYPOTHESES --> ASSESSING_GAPS: more evidence required
    GENERATING_HYPOTHESES --> RANKING: sufficient evidence
    RANKING --> COMPLETED: ranked result stored
    ASSESSING_GAPS --> INCONCLUSIVE: budget exhausted
    COLLECTING_EVIDENCE --> INCONCLUSIVE: required sources unavailable
    GENERATING_HYPOTHESES --> INCONCLUSIVE: evidence insufficient
    RECEIVED --> CANCELLED
    ASSESSING_GAPS --> CANCELLED
    COLLECTING_EVIDENCE --> CANCELLED
    BUILDING_TIMELINE --> CANCELLED
    GENERATING_HYPOTHESES --> CANCELLED
    RANKING --> CANCELLED
    COMPLETED --> [*]
    INCONCLUSIVE --> [*]
    CANCELLED --> [*]
~~~

Terminal states are COMPLETED, INCONCLUSIVE, and CANCELLED. COMPLETED means a valid ranked hypothesis set was produced; it does not mean the operational incident was remediated.

---

## 10. Key Runtime Sequences

### 10.1 Successful Investigation

~~~mermaid
sequenceDiagram
    participant A as Alert Source
    participant API as Control API
    participant O as Orchestrator
    participant T as Tool Gateway
    participant M as LLM Gateway
    participant DB as Evidence Store

    A->>API: Create incident
    API->>DB: Persist incident and audit event
    API->>O: Schedule investigation
    O->>M: Identify missing information
    M-->>O: Structured information needs
    O->>T: Collect logs, metrics, changes, deployments, and pipelines
    T->>DB: Store normalized evidence
    O->>DB: Build and store chronological timeline
    O->>M: Generate hypotheses from incident context
    M-->>O: Hypotheses with support, contradictions, and gaps
    O->>T: Collect discriminating evidence if budget remains
    T->>DB: Store new evidence and update timeline
    O->>M: Revise hypotheses
    M-->>O: Revised structured hypothesis set
    O->>DB: Calculate ranking features and store ranked result
~~~

### 10.2 Insufficient Evidence

1. The system identifies missing information.
2. One or more required sources are unavailable or inconclusive.
3. The agent records which questions remain unanswered.
4. If useful queries remain and budget permits, collection continues.
5. Otherwise, the run ends as INCONCLUSIVE with hypotheses and uncertainty preserved.

### 10.3 Completion Output

1. The final hypothesis set passes schema validation.
2. Every cited evidence ID exists.
3. Supporting and contradicting evidence are separated.
4. Deterministic ranking features are calculated.
5. Hypotheses receive unique ranks.
6. The ranked result and remaining uncertainty are returned for human review.

---

## 11. Data Architecture

### 11.1 Incident

~~~text
id: UUID
external_alert_id: string
service_id: UUID
severity: info | warning | critical
status: workflow state
title: string
alert_message: string
detected_at: timestamp
created_at: timestamp
updated_at: timestamp
resolved_at: timestamp?
autonomy_mode: observe | recommend | controlled
state_version: integer
active_policy_version: string
investigation_budget: JSON
budget_consumed: JSON
~~~

### 11.2 Evidence

~~~text
id: UUID
incident_id: UUID
source_type: logs | metrics | git | deployment | pipeline | health | operator
source_uri: string?
event_time: timestamp?
observed_time: timestamp
ingested_time: timestamp
service_id: UUID?
evidence_type: string
summary: string
structured_payload: JSON
raw_payload_hash: string
reliability: low | medium | high
freshness_seconds: integer?
redaction_status: string
tool_call_id: UUID?
schema_version: string
~~~

### 11.3 Timeline Event

~~~text
id: UUID
incident_id: UUID
event_time: timestamp
time_uncertainty_ms: integer
category: change | deployment | symptom | alert | action | verification
title: string
evidence_ids: UUID[]
~~~

### 11.4 Evidence Relationship

~~~text
id: UUID
incident_id: UUID
from_entity_id: UUID
to_entity_id: UUID
relationship_type:
  PRECEDES | COINCIDES_WITH | SUPPORTS | CONTRADICTS |
  DEPLOYED_FROM | AFFECTS | OBSERVED_ON | PREDICTS
strength: low | medium | high
created_by: deterministic | model | operator
~~~

### 11.5 Hypothesis

~~~text
id: UUID
incident_id: UUID
revision: integer
statement: string
root_cause_category: string
affected_component: string
supporting_evidence_ids: UUID[]
contradicting_evidence_ids: UUID[]
missing_evidence_questions: string[]
prediction: string
evidence_score: decimal
confidence_label: low | medium | high
rank: integer
status: active | weakened | rejected | selected
created_at: timestamp
~~~

### 11.6 Action Proposal

~~~text
id: UUID
incident_id: UUID
hypothesis_id: UUID
action_type: rollback | restart | retry_pipeline | scale
parameters: action-specific JSON
risk: low | medium | high | prohibited
reason: string
supporting_evidence_ids: UUID[]
preconditions: JSON
predicted_signals: JSON
verification_plan: JSON
payload_hash: string
status: proposed | denied | awaiting_approval | approved | expired | executed
~~~

### 11.7 Policy Decision

~~~text
id: UUID
action_proposal_id: UUID
decision: deny | require_approval | allow
policy_version: string
matched_rule_ids: string[]
reason: string
evaluated_at: timestamp
~~~

### 11.8 Approval

~~~text
id: UUID
action_proposal_id: UUID
payload_hash: string
decision: approved | rejected
actor_id: UUID
comment: string?
created_at: timestamp
expires_at: timestamp
~~~

### 11.9 Action Execution

~~~text
id: UUID
action_proposal_id: UUID
idempotency_key: string
target_fingerprint_before: string
status: pending | running | succeeded | failed | timed_out
started_at: timestamp
finished_at: timestamp?
result: JSON
side_effects: JSON
~~~

### 11.10 Verification Result

~~~text
id: UUID
execution_id: UUID
status: succeeded | partial | failed | inconclusive
pre_action_snapshot: JSON
post_action_snapshot: JSON
criteria_results: JSON
prediction_match: JSON
summary: string
created_at: timestamp
~~~

### 11.11 Data Isolation

- Ground truth lives in a separate schema or database.
- The agent database user cannot access ground truth.
- Provider and integration credentials live outside incident storage.
- Raw evidence retention is configurable.
- Normalized evidence and audit metadata can be retained longer than raw logs.

### 11.12 Proposed Retention for the Lab

- Raw simulator evidence: 30 days.
- Normalized evidence and model outputs: 180 days.
- Audit events and final reports: one year.
- Credentials: never stored in incident records or traces.

Real-world retention requires a separate legal and organizational review.

---

## 12. HTTP API

All endpoints use the prefix /api/v1.

### 12.1 Incident Endpoints

~~~text
POST   /api/v1/incidents
GET    /api/v1/incidents
GET    /api/v1/incidents/{incident_id}
POST   /api/v1/incidents/{incident_id}/cancel
POST   /api/v1/incidents/{incident_id}/notes
GET    /api/v1/incidents/{incident_id}/timeline
GET    /api/v1/incidents/{incident_id}/evidence
GET    /api/v1/incidents/{incident_id}/hypotheses
GET    /api/v1/incidents/{incident_id}/report
~~~

Example ingestion:

~~~json
{
  "external_alert_id": "alert-20260912-001",
  "service": "payment-api",
  "severity": "critical",
  "detected_at": "2026-09-12T10:30:00Z",
  "message": "HTTP 500 rate exceeded 20%",
  "labels": {
    "environment": "simulation",
    "region": "local"
  }
}
~~~

### 12.2 Action Endpoints

~~~text
GET    /api/v1/incidents/{incident_id}/actions
POST   /api/v1/actions/{action_id}/approve
POST   /api/v1/actions/{action_id}/reject
GET    /api/v1/actions/{action_id}/execution
GET    /api/v1/actions/{action_id}/verification
~~~

### 12.3 Scenario and Evaluation Endpoints

~~~text
POST   /api/v1/scenarios/{scenario_id}/reset
POST   /api/v1/scenarios/{scenario_id}/start
GET    /api/v1/scenarios/{scenario_id}/status
POST   /api/v1/evaluations
GET    /api/v1/evaluations/{evaluation_id}
GET    /api/v1/evaluations/{evaluation_id}/results
~~~

Scenario endpoints exist only in lab and evaluation profiles.

### 12.4 Error Envelope

~~~json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Action cannot be approved after it expires.",
    "correlation_id": "corr_123",
    "details": {}
  }
}
~~~

---

## 13. Tool Contracts

### 13.1 Investigation Tools

~~~python
get_logs(
    service: str,
    start_time: datetime,
    end_time: datetime,
    severity: str | None,
    pattern: str | None,
    limit: int,
) -> LogQueryResult

get_metrics(
    service: str,
    metric_name: AllowedMetric,
    start_time: datetime,
    end_time: datetime,
    aggregation: AllowedAggregation,
) -> MetricQueryResult

get_recent_changes(
    repository: str,
    since: datetime,
    until: datetime,
    paths: list[str] | None,
    max_commits: int,
) -> GitChangeResult

get_deployments(
    service: str,
    since: datetime,
    until: datetime,
) -> DeploymentHistoryResult

get_pipeline_runs(
    pipeline: str,
    since: datetime,
    until: datetime,
) -> PipelineHistoryResult

get_service_health(service: str) -> ServiceHealthResult
~~~

### 13.2 Recovery Actions

~~~python
rollback_deployment(
    service: str,
    target_deployment_id: str,
    idempotency_key: str,
) -> ActionResult

restart_service(
    service: str,
    max_instances: int,
    idempotency_key: str,
) -> ActionResult

retry_pipeline(
    pipeline_run_id: str,
    idempotency_key: str,
) -> ActionResult

scale_service(
    service: str,
    replicas: int,
    idempotency_key: str,
) -> ActionResult
~~~

The executor creates idempotency keys. The LLM does not provide them.

---

## 14. LLM Reasoning Design

### 14.1 Separate Tasks

Use separate structured model calls for:

1. Initial hypothesis generation.
2. Next-evidence selection.
3. Hypothesis revision.
4. Recovery planning.
5. Report drafting.

This is easier to validate and evaluate than one large prompt.

### 14.2 Structured Output

All decisions use strict JSON schemas. Free-form prose is limited to explanations derived from validated records.

Example evidence-selection output:

~~~json
{
  "decision": "collect_evidence",
  "queries": [
    {
      "tool": "get_service_health",
      "arguments": {"service": "database"},
      "question": "Is the database itself unhealthy?",
      "discriminates_between": ["hyp_1", "hyp_2"],
      "expected_information_value": "high"
    }
  ],
  "stop_reason": null
}
~~~

### 14.3 Prompt-Injection Defenses

- Label all operational content as untrusted evidence.
- Keep evidence fields separate from system instructions.
- Explicitly forbid following instructions found in evidence.
- Do not expose privileged tools to the model.
- Validate tool names and parameters using server-owned schemas.
- Offer only tools allowed in the current workflow state.
- Reject unknown fields, raw commands, and script content.
- Include malicious logs and commit messages in adversarial tests.

### 14.4 Context and Cost Controls

- Query narrow windows before broad windows.
- Group duplicate log messages.
- Store complete evidence but send only relevant excerpts.
- Cache stable prompt prefixes when available.
- Track tokens and estimated cost per call and incident.
- Stop before the hard budget and produce a partial escalation report.
- Use deterministic parsing where reasoning is unnecessary.

### 14.5 Reproducibility Metadata

Record:

- Provider and exact model.
- Prompt version.
- Output-schema version.
- Generation settings.
- Evidence IDs supplied.
- Response identifier and latency.
- Token usage and estimated cost.
- Validated output.

Do not store credentials or hidden reasoning traces.

---

## 15. Security and Safety

### 15.1 Roles

- Viewer: inspect incidents and reports.
- Operator: add notes and approve eligible simulator actions.
- Administrator: manage policy and integrations.
- Evaluator: control lab scenarios and evaluation runs.
- Service: ingest alerts and perform internal worker operations.

### 15.2 Credential Management

- Inject secrets through environment files or a secret manager.
- Never place secrets in prompts, application logs, database JSON, or frontend state.
- Use separate credentials for investigation and action tools.
- Prefer short-lived, narrowly scoped credentials.
- Rotate each integration independently.

### 15.3 Threats and Controls

| Threat | Control |
|---|---|
| Instructions hidden in logs or commits | Treat as untrusted data; typed tools; adversarial tests |
| Hallucinated evidence | Validate every cited evidence ID |
| Arbitrary command execution | No generic shell tool; typed actions only |
| Excessive privileges | Separate read and action identities |
| Replayed approval | Payload hash, expiry, one-time execution |
| Target changes after approval | Recheck fingerprint and preconditions |
| Endless investigation | Step, time, token, cost, and tool budgets |
| Repeated harmful action | Attempt limits, locks, and renewed approval |
| Secret leakage | Redaction before model calls and sanitized telemetry |
| Ground-truth leakage | Separate schema and credentials |
| Audit alteration | Append-only events and hash chaining |
| Provider outage | Bounded retries and safe escalation |

### 15.4 Data Governance Before Real Integration

A production pilot requires decisions about:

- Evidence allowed to leave the organization.
- Tenant and environment isolation.
- Regional processing requirements.
- Retention and deletion.
- Customer data and personal information.
- Redaction test coverage.
- External provider versus self-hosted deployment.

---

## 16. Reliability and Failure Handling

### 16.1 Delivery Semantics

- Alert ingestion is at-least-once and idempotent.
- Read-only tool calls may be retried.
- Actions require explicit idempotency.
- Workers use leases and durable checkpoints.
- No action runs unless its intent and audit event can be persisted.

### 16.2 Failure Matrix

| Failure | Required response |
|---|---|
| Malformed alert | Reject with validation error |
| Duplicate alert | Return or update existing incident |
| Log source unavailable | Record unavailability and continue if possible |
| Stale metrics | Mark stale; do not use for successful verification |
| LLM timeout | Bounded retry, then escalate |
| Invalid model output | One schema-repair attempt, then fail safely |
| Worker crash | Resume from last committed state |
| Policy-engine error | Deny by default |
| Approval expiry | Return to planning or escalate |
| Action timeout | Mark uncertain and require inspection |
| Verification inconclusive | Never mark resolved |
| Database unavailable | Stop; do not execute an unrecorded action |

### 16.3 Safe Defaults

- Policy failure means DENY.
- Audit-write failure blocks mutation.
- Missing fresh verification means INCONCLUSIVE.
- Unknown actions, targets, environments, or parameters are rejected.

---

## 17. Observability

### 17.1 Structured Logs

Each platform log includes:

- UTC timestamp.
- Severity.
- Component.
- Incident ID.
- Correlation ID.
- Workflow state.
- Event name.
- Sanitized fields.

### 17.2 Metrics

Suggested metrics:

~~~text
incidents_created_total
incidents_resolved_total
incidents_escalated_total
incident_time_to_diagnosis_seconds
incident_time_to_recovery_seconds
investigation_rounds
tool_calls_total{tool,status}
tool_call_duration_seconds{tool}
llm_requests_total{model,task,status}
llm_request_duration_seconds{model,task}
llm_tokens_total{model,direction}
llm_estimated_cost_total{model}
action_proposals_total{type,policy_decision}
action_executions_total{type,status}
verification_results_total{status}
policy_denials_total{rule}
~~~

### 17.3 Distributed Tracing

Use OpenTelemetry-compatible trace IDs across:

~~~text
alert ingestion
→ investigation step
→ tool query
→ model request
→ policy decision
→ action execution
→ verification
~~~

Trace data contains IDs and timings, not unrestricted evidence or secrets.

### 17.4 Alerts for the Agent Platform

- Worker queue delay.
- Database errors.
- Model latency or failure spike.
- Repeated invalid structured output.
- Action stuck in an unknown or running state.
- Incident stuck in one workflow state.
- Audit-write failure.
- Cost-budget breach.

---

## 18. Deployment Architecture

### 18.1 Illustrative Local Deployment

The following is one possible local topology, not a required product selection. Any runtime, database, metrics source, log source, and presentation technology may be substituted through the interfaces defined earlier.

~~~mermaid
flowchart LR
    Browser[Browser]
    Provider[External LLM API]

    subgraph Compose[Docker Compose]
        Web[dashboard]
        API[api]
        Worker[worker]
        PG[(postgres)]
        Prom[(prometheus)]
        Grafana[grafana]
        Loki[(loki)]
        Scenario[scenario-controller]

        subgraph Demo[Demo Application]
            Gateway[demo-gateway]
            Payment[payment-api]
            Orders[order-api]
            DemoDB[(demo-db)]
        end
    end

    Browser --> Web
    Web --> API
    API --> PG
    Worker --> PG
    Worker --> Prom
    Worker --> Loki
    Worker --> Scenario
    Worker --> Provider
    Scenario --> Demo
    Demo --> Prom
    Demo --> Loki
    Grafana --> Prom
    Grafana --> Loki
~~~

### 18.2 Network Rules

- Dashboard reaches only the API.
- API and worker reach the configured persistent store.
- Worker reaches data adapters, the model provider, and controlled executor.
- Demo services cannot reach the control database.
- Scenario controller is internal to the lab network.
- Recovery endpoints are not exposed publicly.

### 18.3 Runtime Profiles

- Test: fake integrations and recorded model output.
- Lab: Docker simulator with optional real model calls.
- Evaluation: frozen prompts, settings, seeds, and budgets.
- Future production: reserved; action execution remains disabled until separately reviewed.

---

## 19. Suggested Repository Structure

~~~text
project/
├── ARCHITECTURE.md
├── README.md
├── compose.yaml
├── pyproject.toml
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── domain/
│   │   │   ├── incidents/
│   │   │   ├── evidence/
│   │   │   ├── hypotheses/
│   │   │   ├── actions/
│   │   │   └── verification/
│   │   ├── orchestration/
│   │   ├── llm/
│   │   │   ├── provider.py
│   │   │   ├── openai_provider.py
│   │   │   ├── schemas/
│   │   │   └── prompts/
│   │   ├── tools/
│   │   │   ├── gateway.py
│   │   │   └── adapters/
│   │   ├── policy/
│   │   ├── execution/
│   │   ├── reporting/
│   │   ├── persistence/
│   │   ├── observability/
│   │   └── settings.py
│   ├── migrations/
│   └── tests/
├── dashboard/
│   ├── src/
│   └── tests/
├── simulator/
│   ├── services/
│   ├── scenarios/
│   ├── fixtures/
│   └── controller/
├── evaluation/
│   ├── datasets/
│   ├── graders/
│   ├── runners/
│   └── reports/
└── docs/
    ├── decisions/
    ├── threat-model.md
    └── evaluation-plan.md
~~~

---

## 20. Testing Strategy

### 20.1 Unit Tests

- Legal and illegal state transitions.
- Timeline ordering and timezone normalization.
- Evidence deduplication.
- Secret redaction.
- Evidence-score features.
- Budget enforcement.
- Policy rules.
- Action parameter validation.
- Approval expiry and payload binding.
- Verification thresholds.
- Report claim-to-evidence validation.

### 20.2 Contract Tests

- Every adapter satisfies its output schema.
- Model output satisfies structured schemas.
- Action adapters honor idempotency.
- API error envelopes remain stable.
- Database migrations preserve incident history.

Use recorded source responses for deterministic tests.

### 20.3 Integration Tests

- An alert creates and schedules an incident.
- Tool output becomes evidence and timeline events.
- Hypothesis citations resolve to stored evidence.
- Source replacement preserves the collection contract.
- Reasoning-provider replacement preserves the investigation contract.
- Ranked output contains deterministic score breakdowns.
- A worker restart resumes from its checkpoint.

### 20.4 End-to-End Scenarios

Initial families:

1. Bad database configuration introduced by deployment.
2. Memory exhaustion and container restarts.
3. Dependency incompatibility causing a crash.
4. Real database outage without a relevant deployment.
5. Misleading coincidental deployment.

Each family should contain at least ten reproducible variations, creating a minimum initial benchmark of 50 incidents.

### 20.5 Adversarial Tests

- Log instruction asking the model to ignore policy.
- Commit message requesting a destructive command.
- Fake evidence ID embedded in source data.
- Oversized and repetitive logs.
- Malformed or conflicting timestamps.
- Contradictory health and metric evidence.
- Unavailable source systems.
- Stale verification data.
- Repeated selection of the same query.

### 20.6 Chaos and Recovery Tests

- Kill a worker between transitions.
- Disconnect the configured repository during a checkpoint.
- Delay or fail model responses.
- Disable one data adapter.
- Return partial evidence batches.
- Restart the scenario during evidence collection.

Expected outcome: safe continuation or an explicit inconclusive result, never invented evidence or an endless loop.

---

## 21. Evaluation Design

### 21.1 Compared Systems

1. Human/manual investigation.
2. One LLM prompt containing a fixed evidence bundle.
3. Fixed non-agentic collection plus an LLM conclusion.
4. Tool-using agent without information-gain selection.
5. Complete architecture.
6. Complete architecture without timeline relationships.
7. Complete architecture without contradicting evidence.
8. Optional single-agent versus multi-agent comparison after the MVP.

### 21.2 Dataset Split

- 70% development.
- 15% validation.
- 15% hidden final test.

The final set should include unseen variations and at least one unseen combination of symptoms.

Ground truth contains:

- Root cause.
- Causal chain.
- Relevant evidence.
- Distractors.
- Acceptable actions.
- Prohibited actions.
- Objective recovery criteria.

### 21.3 Metrics

**Diagnosis**

- Top-1 root-cause accuracy.
- Top-3 root-cause recall.
- Mean reciprocal rank.
- Causal-chain partial-credit score.
- Appropriate abstention rate.

**Evidence**

- Relevant-evidence precision and recall.
- Unsupported factual claim rate.
- Invalid-citation rate.
- Contradiction-recognition rate.
- Unnecessary tool-call rate.

**Investigation safety**

- Invalid tool-query rate.
- Cross-incident evidence-citation rate.
- Prompt-injection success rate.
- Fabricated-evidence rate.
- Budget-overrun rate.

**Efficiency**

- Time to diagnosis.
- Time to ranked result.
- Investigation rounds.
- Tool calls.
- Input and output tokens.
- Estimated model cost.

**Reliability**

- Workflow completion rate.
- Structured-output failure rate.
- Loop-exhaustion rate.
- Variation across repeated runs.

### 21.4 Proposed MVP Targets

- At least 85% top-1 root-cause accuracy.
- Zero cross-incident or fabricated evidence citations in accepted output.
- Zero write or remediation tool calls.
- At least 90% of factual report claims linked to valid evidence.
- At least 30% lower median diagnosis time than the defined manual baseline.

These are research targets. Pilot results may justify documented revisions before final evaluation.

### 21.5 Experimental Controls

- Freeze model, prompt, schema, policy, and budget versions per run.
- Use deterministic simulator seeds.
- Repeat stochastic configurations.
- Report medians, means, dispersion, and confidence intervals.
- Separate tuning data from hidden final evaluation.
- Record failures, not only successful demonstrations.
- Prevent incident-memory leakage.

---

## 22. Performance Assumptions

The MVP targets research-scale use:

- Up to ten active incidents.
- Up to 100 normalized evidence records per incident before compression.
- Up to twelve investigation tool calls per incident by default.
- Up to six evidence-gathering rounds by default.
- Dashboard updates within approximately two seconds of committed state changes.

These are capacity assumptions, not production service-level objectives.

Later scaling options include a dedicated queue, object storage, horizontally scaled workers, and streaming event ingestion.

---

## 23. Innovation in the Architecture

### 23.1 Information-Gain-Driven Collection

The query planner chooses evidence that best distinguishes competing hypotheses. Evaluation compares this with fixed retrieval and collecting everything.

### 23.2 Temporal and Causal Evidence Graph

Stored relationships allow users to inspect how a commit, deployment, symptom, and alert are connected.

### 23.3 Testable Predictions During Investigation

Each hypothesis records an observation that should exist if it is correct. The query planner can seek that evidence, and the next revision can strengthen or weaken the hypothesis.

### 23.4 Calibrated Abstention

The system can decline to diagnose or act when evidence is insufficient. Confidence labels can later be calibrated against observed correctness.

### 23.5 Hybrid Intelligence

The LLM handles ambiguous synthesis; deterministic code handles calculations, policy, authorization, and success criteria.

### 23.6 Evidence-Linked Reporting

Every factual conclusion links to immutable evidence, supporting review, auditing, and research scoring.

---

## 24. Implementation Phases

### Phase 1: Contracts and Foundation

**Deliverables**

- Repository and development tooling selected by the team.
- Versioned shared input and output contracts.
- Replaceable repository interfaces with an in-memory implementation.
- Core incident schemas and investigation state machine.
- Structured logging and tests.

**Acceptance criteria**

- One documented command starts the selected local runtime.
- An alert creates a durable incident.
- Invalid state transitions are rejected.
- Contracts round-trip through schema validation.

### Phase 2: Collection

**Deliverables**

- Source-neutral interfaces for logs, metrics, code changes, deployments, pipelines, and configuration changes.
- Fixture adapters for all six sources.
- Query validation, capability discovery, timeouts, and result limits.
- Recorded collection fixtures.

**Acceptance criteria**

- Every collector returns the common result contract.
- A failed source produces a structured error rather than crashing the investigation.
- Replacing a fixture adapter does not change collection-service contracts.

### Phase 3: Evidence and Timeline

**Deliverables**

- Normalization, redaction, deduplication, and provenance.
- Replaceable evidence repositories.
- Chronological timeline and temporal relationships.
- Incident context snapshots.

**Acceptance criteria**

- Evidence from all source types maps to the canonical schema.
- Replayed batches do not create duplicates.
- Timeline order is deterministic.
- Every timeline event references stored evidence.

### Phase 4: Reasoning and Ranking

**Deliverables**

- Replaceable ReasoningProvider interface.
- Missing-information assessment.
- Evidence-query planning.
- Bounded investigation loop.
- Hypothesis generation and revision.
- Citation validation and deterministic ranking.

**Acceptance criteria**

- The system reports what information is missing.
- Multiple hypotheses cite supporting and contradicting evidence.
- Invalid citations are rejected.
- Identical inputs produce the same final numeric ranking.
- The workflow stops after RankedHypothesisSet.

### Phase 5: Integration and Evaluation

**Deliverables**

- Pairwise contract tests.
- End-to-end recorded replay.
- At least five incident families with reproducible variants.
- Baselines, ablations, graders, and result export.

**Acceptance criteria**

- A recorded incident runs from alert to ranked hypotheses.
- Results cover diagnosis, evidence quality, efficiency, and reliability.
- Development and hidden final-test results remain separate.

---

## 25. Required Architecture Decision Records

Create short records in docs/decisions for:

1. ADR-001: Investigation workflow and stopping states.
2. ADR-002: Repository and work-queue abstraction boundaries.
3. ADR-003: ReasoningProvider contract and routing profiles.
4. ADR-004: Evidence schema and graph relationships.
5. ADR-005: Confidence terminology and calibration.
6. ADR-006: Source adapter capabilities and query limits.
7. ADR-007: Investigation budgets and stopping rules.
8. ADR-008: Scenario and ground-truth isolation.
9. ADR-009: Evidence retention and redaction.
10. ADR-010: Evaluation split and success thresholds.

This document supplies recommended defaults. ADRs should record implementation consequences and any later changes.

---

## 26. Major Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Agent always blames the latest deployment | Misdiagnosis | Include true outages and misleading deployments in every split |
| Uncalibrated confidence | False trust | Use evidence score and permit abstention |
| Prompt injection | Corrupted investigation | Isolate evidence, use typed read-only tools, and validate outputs |
| Excessive context | Cost and poorer reasoning | Targeted retrieval, deduplication, budgets |
| Framework lock-in | Difficult experiments | Project-owned domain and provider interfaces |
| Simulator too simple | Weak research result | Add distractors, missing data, and combined faults |
| Ground-truth leakage | Inflated accuracy | Separate schema, roles, and fixtures |
| Model variability | Noisy evaluation | Record versions and repeat runs |
| Scope growth | Delayed core result | Finish the control loop before Kubernetes and integrations |

---

## 27. Definition of Done

The complete prototype must demonstrate:

~~~text
Alert received
→ incident persisted
→ evidence collected through typed tools
→ timeline assembled
→ multiple hypotheses generated
→ support and contradictions cited
→ root causes ranked
→ uncertainty reported
→ ranked result returned for human review
→ result scored against hidden ground truth
~~~

It must also provide:

- Reproducible local startup.
- Automated unit, contract, integration, adversarial, and scenario tests.
- Read-only tools only; no remediation path.
- A benchmark with baselines and ablations.
- Versioned prompts, schemas, policies, and architecture decisions.

---

## 28. Immediate Implementation Milestone

The first milestone should create:

1. A technology-neutral contracts package.
2. IncidentAlert, IncidentSeed, RawEvidenceBatch, IncidentContextSnapshot, and RankedHypothesisSet schemas.
3. Replaceable repository and reasoning-provider interfaces.
4. The reduced investigation state machine.
5. In-memory or fixture implementations for independent development.
6. A runner that advances an incident through a mocked investigation.
7. Contract and state-transition tests.

This establishes stable boundaries before any database, model, framework, or source product is selected. The next milestones follow the three-person ownership plan in WORK_DIVISION.md.
