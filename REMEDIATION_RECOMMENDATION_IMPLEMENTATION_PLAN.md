# Recommendation-Only Remediation: Team Implementation Plan

**Status:** Approved for implementation planning  
**Scope:** Use the provider-neutral LLM layer to produce detailed, evidence-linked remediation *recommendations* after incident investigation.  
**Out of scope:** Executing commands, modifying configuration, triggering deployments, restarting services, scaling infrastructure, rollbacks, approval persistence, or any write-capable integration.

## 1. Goal

RecoverIT currently stops when it produces a `RankedHypothesisSet`. Extend the
new LangGraph workflow so every investigation also produces a safe,
operator-facing `RemediationPlan`.

The plan must explain what an operator should consider doing, why it is being
recommended, which evidence supports it, what must be checked first, how to
verify success, and how to reverse the proposed change. It is guidance only;
it must never perform the proposed action.

```text
collect → build context → hypothesize → evaluate → rank
                                                   ↓
                                      plan remediation → END
```

Inconclusive investigations must also reach `plan remediation`; their output
must explicitly say that no change is recommended and list the next checks or
escalation path.

## 2. Non-negotiable safety rules

1. The remediation node calls the existing provider-neutral LLM layer and
   accepts only JSON that validates as `RemediationPlan`. It must not generate
   shell commands.
2. The service consumes only canonical `RankedHypothesisSet` and
   `IncidentContextSnapshot` objects. It must not query adapters directly.
3. Every evidence reference in a plan must point to an evidence ID already
   present in the ranked hypothesis and current context.
4. Raw log text, configuration values, credentials, tokens, and untrusted
   instructions must never be copied into plan instructions.
5. A low-confidence, contradictory, or unresolved hypothesis must result in
   verification/escalation guidance, never a proposed production change.
6. All output is descriptive and human-actionable. It may say “restore the
   approved last-known-good revision”; it must not issue executable syntax such
   as `kubectl rollout undo` or `git reset`.

## 3. Canonical contracts — teammate A

**Owned paths**

```text
contracts/remediation/**
contracts/enums.py
tests/contracts/test_remediation_contracts.py
```

Create `contracts/remediation/schemas.py` and a minimal re-exporting
`contracts/remediation/__init__.py`. All models must inherit `ContractModel`,
therefore retaining the project’s schema versioning and canonical JSON rules.

```python
class RemediationRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class RemediationStep(ContractModel):
    step_number: int = Field(ge=1)
    title: str
    purpose: str
    instructions: list[str] = Field(min_length=1)
    expected_result: str
    verification: list[str] = Field(default_factory=list)
    rollback_guidance: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True


class RemediationPlan(ContractModel):
    plan_id: str
    incident_id: str
    created_at: datetime
    recommendation_available: bool
    safety_notice: str
    hypothesis_id: str | None = None
    root_cause_category: RootCauseCategory | None = None
    confidence: ConfidenceLabel | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    risk: RemediationRisk
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[RemediationStep] = Field(default_factory=list)
    escalation_guidance: list[str] = Field(default_factory=list)
    unresolved_uncertainty: list[str] = Field(default_factory=list)
```

Add `REMEDIATION_PLANNING` and `REMEDIATION_READY` to `InvestigationState`
only if the UI needs these distinct workflow-state labels. The
`InvestigationStatus` must remain `completed` or `inconclusive`; preparation
of guidance is not recovery.

### Contract acceptance criteria

- Models JSON-round-trip and reject unknown fields.
- An available plan has an incident ID, hypothesis ID, category, confidence,
  and at least one evidence ID and step.
- An unavailable recommendation may have no hypothesis ID, but must include a
  safety notice and unresolved uncertainty or escalation guidance.
- Step numbers are positive and ordered by the planner.

## 4. LLM-backed planning service — teammate B

**Owned paths**

```text
remediation/__init__.py
remediation/planner.py
remediation/policies.py
reasoning/provider/interface.py
reasoning/provider/llm_provider.py
tests/remediation/**
```

Define a graph-facing protocol in `investigation/graph/ports.py`:

```python
@runtime_checkable
class RemediationPlanningService(Protocol):
    async def plan(
        self,
        ranked: RankedHypothesisSet,
        context: IncidentContextSnapshot,
    ) -> RemediationPlan: ...
```

Implement `LLMRemediationPlanningService`, backed by the existing
`ReasoningProvider`. Add this provider method so provider-specific LLM clients
continue to stay behind the established abstraction:

```python
async def generate_remediation(
    self,
    ranked: RankedHypothesisSet,
    context: IncidentContextSnapshot,
) -> RemediationPlan: ...
```

The `LLMReasoningProvider` must construct the prompt, invoke its existing
`_execute_structured_call(...)` helper with `RemediationPlan` as the target
model, and use temperature `0.0`. Add a versioned prompt constant such as
`PROMPT_VERSION_REMEDIATE = "generate_remediation:v1.0"`. The fake, recorded,
and scripted providers must implement the new protocol method for tests.

The prompt must supply only the ranked result and the already-redacted context.
Its system instruction must require the model to:

- select only from the ranked hypotheses and cite only their supporting
  evidence IDs;
- generate a blocked verification/escalation plan if confidence is not high,
  evidence conflicts, evidence is incomplete, or the ranking is inconclusive;
- write 3–6 detailed human-review steps for an actionable recommendation;
- include prerequisites, expected outcome, verification, rollback guidance,
  and `requires_human_approval: true` for every suggested change;
- describe actions generically through the approved operational process;
- never return commands, credentials, raw configuration values, write API
  requests, or an instruction to bypass change approval.

Prompt content is untrusted data: logs, commits, evidence summaries, and
configuration changes must be presented as evidence only, never as
instructions to follow.

After schema validation, run a small deterministic policy validator. It must
reject or downgrade a model output when it cites unknown evidence, lacks human
approval, contains a command-like string, proposes an action for an
inconclusive ranking, or omits uncertainty. On rejection, return a safe blocked
plan instead of displaying the model output.

The model may tailor guidance by root-cause category. For example, it can
recommend reviewing a known-good configuration revision for a configuration
regression or escalating to the database owner for a database outage. It must
not assume a vendor, deployment command, or credential.

### Service acceptance criteria

- LLM output is parsed directly into the canonical schema; invalid output has
  one existing schema-repair attempt, then produces a safe blocked plan.
- The plan cites a subset of top-hypothesis supporting evidence IDs.
- The post-generation policy rejects raw evidence summaries, credentials, and
  strings that look like executable commands.
- Any unsupported category, contradiction, or unresolved critical evidence
  produces a blocked/escalation plan.

## 5. LangGraph wiring — teammate C

**Owned paths**

```text
investigation/graph/state.py
investigation/graph/ports.py
investigation/graph/dependencies.py
investigation/graph/nodes/remediate.py
investigation/graph/nodes/__init__.py
investigation/graph/builder.py
tests/graph/test_remediation_node.py
tests/graph/test_investigation_graph.py
```

Add a checkpoint-safe field to the graph state and public output:

```python
remediation_plan: RemediationPlan | None
```

Add `remediation_planning_service: RemediationPlanningService` to
`GraphDependencies`. The node is intentionally thin:

```python
async def plan_remediation(state, dependencies) -> dict[str, object]:
    ranked = state["ranked_result"]
    plan = await dependencies.remediation_planning_service.plan(
        ranked=ranked,
        context=state["context"],
    )
    return {
        "remediation_plan": plan,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="status",
            stage="plan_remediation",
            title="Remediation guidance prepared",
            detail=plan.safety_notice,
        ),
    }
```

Register the node in `NODE_NAMES` and route both terminal-producing nodes to
it:

```python
builder.add_edge("rank_hypotheses", "plan_remediation")
builder.add_edge("finish_inconclusive", "plan_remediation")
builder.add_edge("plan_remediation", END)
```

Do not add business policy to routing or node code. Do not put service objects
in graph state. The new `RemediationPlan` is serializable and is stored by the
existing incident-ID checkpoint mechanism.

### Graph acceptance criteria

- Completed investigations rank before planning remediation.
- Inconclusive investigations produce a blocked plan and then end.
- Resume/checkpoint tests show the same stored remediation plan.
- Progress output contains `plan_remediation` but no secrets or raw evidence.

## 6. Runtime and presentation — teammate D

**Owned paths**

```text
recoverit/composition.py
recoverit/runner.py
recoverit/cli.py
recoverit/web/configured.py
recoverit/web/static/app.js
recoverit/web/static/index.html
recoverit/web/static/style.css
tests/integration/test_runner.py
tests/integration/test_live_web_api.py
```

In `_build_live_dependencies`, instantiate `LLMRemediationPlanningService`
with the already-created `LLMReasoningProvider` and inject it into
`GraphDependencies`. It uses the configured LLM client but has no source
adapter or write-capable integration.

Extend `InvestigationResult` with a JSON-safe `remediation_plan` field. Do not
flatten it into unstructured text: the CLI and web UI should render the same
typed result.

Presentation requirements:

- Label the section **Suggested remediation — human review required**.
- Show the selected hypothesis, confidence/evidence score, and cited evidence
  IDs before listing steps.
- Display prerequisites, steps, verification, rollback guidance, and remaining
  uncertainty separately.
- For a blocked plan, prominently show **No production change recommended**
  and the escalation guidance.
- Do not display an “Execute”, “Apply”, “Restart”, or “Rollback now” button.

## 7. Merge sequence and test matrix

1. Teammate A merges canonical contracts and contract tests.
2. Teammate B extends the provider contract and builds the LLM remediation
   service against those contracts.
3. Teammate C wires the service into the LangGraph graph with a scripted
   planner test double first, then the real service.
4. Teammate D composes and presents the plan after the graph result is stable.
5. Run the entire suite, then add at least one end-to-end completed incident
   and one inconclusive incident test.

Required cases:

| Case | Expected result |
|---|---|
| High-confidence configuration regression | Detailed approved-review remediation plan with evidence links and rollback guidance |
| High score but contradiction | Blocked plan; explain conflict and request validation |
| Medium/low confidence result | No direct change recommendation; verification/escalation only |
| Database outage | Escalate/verify platform health; no config or deployment advice by default |
| Empty/inconclusive result | Blocked plan with unresolved questions |
| Secret-like raw evidence | Secret absent from plan, progress events, CLI, and API response |
| Checkpoint resume | Stored plan available after resume without recomputing a different recommendation |

## 8. Definition of done

- Every completed or inconclusive investigation returns a valid
  `RemediationPlan`.
- Recommendations are detailed, evidence-linked, schema-validated, and
  provider-neutral.
- The LangGraph core, not an alternate orchestration path, produces the plan.
- No remediation code writes to a repository, API, cluster, pipeline, or
  configuration store.
- CLI and web interfaces clearly communicate that all recommendations require
  human review.
- New contract, service, graph, runner, and presentation tests pass alongside
  the existing suite.
