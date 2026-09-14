# RecoverIT Credibility and Explainability Improvement Plan

## Objective

Make RecoverIT demonstrably agentic, evidence-led, and explainable. The system should investigate a neutral incident alert, execute meaningful source queries, distinguish symptoms from causes, test competing explanations, and clearly justify its final ranking.

The completed system should support the following claim:

> RecoverIT does not receive the root cause in its prompt. It plans an investigation, queries independent evidence sources, evaluates competing hypotheses, validates citations, and ranks conclusions using reproducible scoring rules.

## Current Issues

The current implementation successfully invokes a live LLM and runs the orchestration loop, but several details weaken the credibility of the demonstration:

1. Scenario names and incident summaries reveal the expected root cause.
2. Fixture adapters return prerecorded source records without applying most displayed query filters.
3. Symptom evidence can be treated as sufficient support for a causal claim.
4. Semantically equivalent hypotheses can appear as separate ranked results.
5. Stopping rules allow the investigation to finish before direct causal evidence is established.
6. Confidence scores are shown without enough explanation of how they were calculated.
7. The CLI shows actions and results, but it should connect each observation to the agent's next decision more explicitly.

---

## Phase 1 — Remove Answer Leakage

### Goal

Give the agent only observable symptoms rather than the known root cause.

### Changes

- Replace revealing incident titles such as:

  ```text
  Bad database configuration introduced by deployment
  ```

  with neutral alerts such as:

  ```text
  [P1] HTTP 500 rate increased to 35% on payment-api
  ```

- Remove scenario names from model-visible incident labels.
- Keep the expected cause as separate, hidden benchmark metadata.
- Replace revealing public scenario IDs with neutral identifiers such as `incident_001`.
- Preserve internal aliases if backward compatibility is needed.

### Likely Files

- `collectors/fixtures/data/*.json`
- `recoverit/runner.py`
- `recoverit/cli.py`

### Acceptance Criteria

- The LLM receives only the alert, service, environment, severity, and detection time.
- The model-visible prompt does not contain `bad_db_config` or the expected diagnosis.
- Ground truth is unavailable to the investigation loop.
- Ground truth can only be revealed after the investigation for benchmark evaluation.

---

## Phase 2 — Make Fixture Queries Genuine

### Goal

Ensure that source adapters return only records that match the displayed query.

### Required Filtering

| Source | Filters to implement |
|---|---|
| Logs | `service`, `pattern`, `start_time`, `end_time`, `limit` |
| Metrics | `service`, `metric_name`, time window, `aggregation`, `limit` |
| Changes | `paths`, `since`, `until`, `max_commits` |
| Deployments | `service`, `since`, `until`, `limit` |
| Pipelines | pipeline/service, status, time window, `limit` |
| Configuration | service, key, time window, `limit` |

When no records match, return a successful empty result instead of unrelated records:

```text
status: ok
records: 0
reason: No records matched the requested filters.
```

### Likely Files

- `collectors/fixtures/base_fixture_adapter.py`
- Source-specific fixture adapters under `collectors/`
- `tests/unit/test_fixture_adapters.py`

### Acceptance Criteria

- A `response_time` metrics query cannot return `http_500_rate`.
- A nonmatching log pattern returns zero log records.
- A Git path filter only returns matching commits or files.
- Service and time-window filters are applied consistently.
- Every advertised query parameter has a corresponding automated test.

---

## Phase 3 — Improve Evidence Planning

### Goal

Make the model seek evidence that can prove or disprove causation rather than only confirming symptoms.

### Changes

- Classify information gaps as:
  - Symptom confirmation
  - Temporal correlation
  - Direct causal evidence
  - Contradicting evidence
- Require direct-cause queries for change-related hypotheses.
- Prefer neutral questions such as:

  ```text
  Were any relevant configuration or code changes introduced before the alert?
  ```

  instead of leading questions such as:

  ```text
  Which deployment introduced the bad configuration?
  ```

- Require at least one query that can disprove the leading hypothesis.
- Query configuration history when investigating a possible configuration regression.
- Carry unresolved high-priority gaps into subsequent rounds.

### Likely Files

- `reasoning/provider/llm_provider.py`
- `investigation/query_planning/planner.py`
- Investigation schemas and their tests

### Acceptance Criteria

- Query questions do not assume a deployment or configuration change is causal.
- Configuration or change evidence is requested when relevant.
- At least one alternative explanation is actively tested.
- The planner explains which gap and hypothesis each query addresses.

---

## Phase 4 — Strengthen Citation Validation

### Goal

Prevent symptom evidence from being presented as direct proof of a root cause.

### Evidence Roles

Add a role to each citation:

```text
cause
effect
correlation
contradiction
context
```

### Validation Rules

- A `configuration_regression` hypothesis must cite configuration or code-change evidence.
- A `deployment_failure` hypothesis must cite deployment evidence.
- Logs and metrics can prove impact but cannot independently prove that a deployment caused it.
- Supporting evidence must semantically match the claim it supports.
- Unsupported causal claims must remain assumptions or receive a substantial score penalty.
- Contradicting evidence must be retained and displayed.

### Likely Files

- `contracts/hypothesis/schemas.py`
- `reasoning/hypotheses/citation_validator.py`
- `reasoning/ranking/feature_calculators.py`

### Acceptance Criteria

- Logs and metrics alone cannot produce HIGH confidence for a configuration regression.
- A configuration-regression hypothesis becomes strongly supported only after citing the relevant change.
- Invalid causal citations produce a visible validation warning.
- Citation validation checks relevance as well as existence.

---

## Phase 5 — Deduplicate Hypotheses

### Goal

Prevent multiple ranks from presenting the same explanation with different wording.

### Approach

- Normalize root-cause category and affected component.
- Compare normalized statements for semantic similarity.
- Merge equivalent hypotheses before ranking.
- Combine unique supporting and contradicting citations.
- Preserve genuinely different alternative explanations.

For example, these should be merged:

```text
The database connection configuration is incorrect.
```

```text
There is a misconfiguration in the database service.
```

### Likely Files

- New `reasoning/hypotheses/deduplicator.py`
- Hypothesis generation or pre-ranking integration
- Hypothesis deduplication tests

### Acceptance Criteria

- No two ranked hypotheses are semantic duplicates.
- Merged citations remain valid and unique.
- Different explanations, such as configuration regression and infrastructure outage, remain separate.

---

## Phase 6 — Tighten Stopping Rules

### Goal

Continue investigating until the cause, rather than only the symptoms, has adequate support.

### Proposed Completion Requirements

- At least two independent evidence source types.
- At least one direct causal record.
- At least one symptom or impact record.
- No unresolved HIGH-priority information gaps.
- The leading hypothesis exceeds the configured confidence threshold.
- The leading hypothesis has a meaningful score advantage over rank two.
- At least one alternative explanation has been tested.

Remove the permissive one-source stopping override from the application runner.

### Likely Files

- `investigation/orchestration/orchestrator.py`
- `recoverit/runner.py`
- `tests/investigation/test_orchestrator.py`

### Acceptance Criteria

- Logs plus metrics alone cannot finish a configuration investigation.
- The agent starts another round when direct causal evidence is missing.
- The CLI explains which completion conditions were met or remain unresolved.
- Budget exhaustion is clearly distinguished from sufficient evidence.

---

## Phase 7 — Make Confidence Explainable

### Goal

Make every final score reproducible and understandable.

### Example CLI Output

```text
Confidence breakdown
  Independent sources       +21.25
  Symptom coverage           +15.00
  Temporal consistency       +15.00
  Direct change evidence     +15.00
  Specificity                 +8.00
  Contradictions               0.00
  Missing causal evidence      0.00
  Final score                 74.25
```

Consider displaying separate values for:

- Symptom confidence
- Causal confidence
- Overall investigation confidence

### Likely Files

- `reasoning/ranking/ranking_engine.py`
- `reasoning/ranking/feature_calculators.py`
- `recoverit/cli.py`

### Acceptance Criteria

- Every score can be reproduced from the displayed breakdown.
- Missing causal evidence visibly reduces confidence.
- HIGH confidence cannot be assigned without direct causal support.
- Penalties and contradictions are displayed, not hidden.

---

## Phase 8 — Improve the CLI Investigation Trace

### Goal

Connect each observation to the agent's next decision so that the investigation loop is clearly visible.

### Desired Trace Format

```text
RATIONALE
Need to determine whether a relevant change preceded the alert.

TOOL CALL
changes.search(service=payment-api, since=..., paths=[...])

TOOL RESULT
1 matching commit
abc1234 — Update database connection configuration

INTERPRETATION
The commit preceded the alert by 15 minutes, but causation is not yet proven.

NEXT DECISION
Query deployment and configuration history to determine whether the commit reached production.
```

### Additional Metadata

- Adapter type: `Recorded Replay` or `Live Source`
- Reasoning provider: `Live Ollama`, `Gemini`, or `Offline Preset`
- Investigation round
- Tool latency
- Records matched
- Filters actually applied
- Truncation and warnings
- LLM call latency and token counts, when available

### Likely Files

- `investigation/orchestration/orchestrator.py`
- `recoverit/cli.py`
- `recoverit/runner.py`

### Acceptance Criteria

- Every tool call has a rationale, input, result, interpretation, and next decision.
- Tool output is visually distinct from model-generated interpretation.
- Recorded fixture data is never presented as a live external system response.
- Long output is summarized without hiding record counts or warnings.

---

## Phase 9 — Add Hidden-Ground-Truth Evaluation

### Goal

Measure whether the agent discovered the expected cause without exposing it during the investigation.

### Proposed Option

```bash
python -m recoverit.cli run \
  --scenario incident_001 \
  --mode live \
  --provider ollama \
  --model qwen2.5:7b \
  --reveal-ground-truth
```

### Example Evaluation

```text
Benchmark evaluation
  Expected category: Configuration Regression
  Predicted category: Configuration Regression
  Category match: Yes
  Expected causal record: cfg-chg-01
  Cited by leading hypothesis: Yes
```

### Likely Files

- Fixture benchmark metadata
- New benchmark evaluator
- `recoverit/cli.py`
- Benchmark evaluation tests

### Acceptance Criteria

- Expected results are inaccessible to prompts and runtime reasoning.
- Evaluation occurs only after the investigation is complete.
- Category accuracy and causal-record citation are reported separately.
- Evaluation can be disabled for normal use.

---

## Recommended Implementation Order

1. Neutralize model-visible incident input.
2. Implement genuine fixture filtering.
3. Improve information-gap assessment and query planning.
4. Add evidence roles and causal citation validation.
5. Deduplicate hypotheses.
6. Tighten stopping rules.
7. Expose the confidence breakdown.
8. Improve the CLI reasoning-to-action trace.
9. Add hidden-ground-truth benchmark evaluation.
10. Run every scenario using both offline and live-Ollama modes.

## Testing Strategy

### Unit Tests

- Every source filter and limit
- Evidence-role validation
- Causal citation requirements
- Hypothesis deduplication
- Ranking feature calculations
- Stopping-rule combinations
- Ground-truth isolation

### Integration Tests

- Neutral alert to final diagnosis
- Empty query results
- Source timeout or unavailability
- Incorrect leading hypothesis followed by revision
- Multi-round investigation
- Competing deployment and non-deployment explanations
- Ollama structured-output failure and retry

### Demonstration Tests

- Run the same incident with two different supported models.
- Stop Ollama and confirm that live mode fails or becomes inconclusive clearly.
- Compare offline preset behavior with live reasoning behavior.
- Demonstrate a coincidental deployment that is not the root cause.
- Reveal hidden ground truth only after the final answer.

## Definition of Done

This improvement is complete when:

- The model is never given the expected root cause.
- Displayed query filters are actually enforced.
- Every causal conclusion cites direct causal evidence.
- Equivalent hypotheses are merged.
- Stopping requires meaningful causal support.
- Confidence scores are transparent and reproducible.
- The CLI distinguishes model reasoning, tool actions, recorded observations, and deterministic ranking.
- Benchmark results demonstrate that the correct cause was discovered rather than supplied.
