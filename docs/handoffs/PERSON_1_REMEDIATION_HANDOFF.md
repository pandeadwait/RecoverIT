# Person 1 Remediation Handoff Document

## 1. Overview
- **Role**: Person 1 (Core Contracts, Safety Policies, Reasoning Provider Extension, and Remediation Planning Service)
- **Branch**: `code-generation-person-1`
- **Primary Implementation Commit SHA**: `ebbac337c18f82417d262950408caf43e27c4ddf`
- **Parent Branch**: `origin/cleanup`

---

## 2. Summary of Changes & Artifacts

### 2.1 Contracts (`contracts/remediation/`)
- `contracts/remediation/schemas.py`: Canonical Pydantic v2 data models conforming to `ContractModel` (`extra="forbid"`, `schema_version="1.0"`):
  - `RemediationRisk` (`low`, `medium`, `high`, `blocked`): Categorical operational risk tier.
  - `RemediationStep`: Granular, human-actionable instructions with mandatory `requires_human_approval=True`, purpose, expected results, verification, and rollback guidance.
  - `RemediationPlan`: Structured remediation plan. Enforces that `recommendation_available=True` requires `hypothesis_id`, `root_cause_category`, `confidence`, non-empty `evidence_ids`, and at least one step in `steps`. For `recommendation_available=False`, enforces `risk=RemediationRisk.BLOCKED`, empty `steps`, and populated `escalation_guidance` or `unresolved_uncertainty`.
- `contracts/remediation/__init__.py`: Package export for schemas.

### 2.2 Safety Policies (`remediation/policies.py`)
- `validate_and_sanitize_remediation_plan(plan, ranked, context) -> RemediationPlan`: Deterministic safety engine executed after provider reasoning:
  - **Command / Executable Syntax Detection**: Scans all step fields for commands (`kubectl`, `docker`, `systemctl`, `helm`, `git reset`, `rm -rf`, `DROP TABLE`, etc.) and automatically downgrades to a safe `BLOCKED` plan.
  - **Evidence Grounding Validation**: Ensures every cited evidence ID exists in the hypothesis citations or context evidence snapshot. Ungrounded IDs trigger safe `BLOCKED` downgrades.
  - **Confidence & Contradiction Thresholding**: Only `ConfidenceLabel.HIGH` hypotheses without contradicting evidence can produce available recommendations. Any other tier downgrades to `BLOCKED`.
  - **Credential / Secret Sanitization**: Scrubs tokens and password patterns.
- `create_blocked_remediation_plan(...)` & `create_inconclusive_remediation_plan(...)`: Standardized safe fallback plan constructors requiring human triage.

### 2.3 Reasoning Provider Extension (`reasoning/provider/`)
- `reasoning/provider/interface.py`: Extended `ReasoningProvider` protocol with:
  ```python
  async def generate_remediation(
      self,
      ranked: RankedHypothesisSet,
      context: IncidentContextSnapshot,
  ) -> RemediationPlan: ...
  ```
- `reasoning/provider/llm_provider.py`:
  - Added `PROMPT_VERSION_REMEDIATE = "generate_remediation:v1.0"`.
  - Implemented structured output generation targeting `RemediationPlan` at `temperature=0.0`.
  - Includes transient retries, schema repair fallback, and system instructions prohibiting shell commands.
- `tests/support/scripted_reasoning_provider.py` & `reasoning/provider/fake_provider.py`:
  - Implemented deterministic `generate_remediation` supporting custom scripted plans and heuristic synthesis.
- `reasoning/provider/recorded_provider.py`:
  - Implemented deterministic replay lookup for `generate_remediation`.

### 2.4 Planning Service (`remediation/planner.py`)
- `LLMRemediationPlanningService`:
  ```python
  class LLMRemediationPlanningService:
      def __init__(self, reasoning_provider: ReasoningProvider) -> None: ...
      async def plan(
          self,
          ranked: RankedHypothesisSet | None,
          context: IncidentContextSnapshot | None,
      ) -> RemediationPlan: ...
  ```
  - Inconclusive/incomplete investigations immediately return a safe inconclusive plan.
  - Generates candidate plan via `reasoning_provider.generate_remediation`.
  - Catches provider runtime failures and produces safe `BLOCKED` plans.
  - Enforces policy validation via `validate_and_sanitize_remediation_plan`.
- `remediation/__init__.py`: Package export.

### 2.5 Test Doubles & Comprehensive Test Suite
- `tests/support/scripted_remediation_planner.py`: `ScriptedRemediationPlanningService` test double for Person 2's LangGraph node and integration testing.
- `tests/contracts/test_remediation_contracts.py`: 7 tests covering contract serialization, schema invariants, human approval enforcement, and step numbering.
- `tests/remediation/test_policies.py`: 7 tests verifying policy enforcement (commands, evidence grounding, confidence downgrade, secrets).
- `tests/remediation/test_planner.py`: 7 tests verifying `LLMRemediationPlanningService` and `ScriptedRemediationPlanningService`.

---

## 3. Test Verification Results

All new and pre-existing tests pass with zero regressions:
```bash
.\pjtVenv\Scripts\python.exe -m pytest tests/contracts/test_remediation_contracts.py tests/remediation/ -v
# 21 passed in 0.37s

.\pjtVenv\Scripts\python.exe -m pytest -q
# 492 passed, 40 subtests passed in 13.91s
```

---

## 4. Integration Guide for Person 2

Person 2 can integrate directly with Person 1's work without writing any glue or adapter code:

### 4.1 Protocol Definition (`investigation/graph/ports.py`)
```python
from typing import Protocol, runtime_checkable
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import RankedHypothesisSet
from contracts.remediation.schemas import RemediationPlan

@runtime_checkable
class RemediationPlanningService(Protocol):
    async def plan(
        self,
        ranked: RankedHypothesisSet | None,
        context: IncidentContextSnapshot | None,
    ) -> RemediationPlan: ...
```

### 4.2 Runtime Composition (`recoverit/composition.py`)
```python
from remediation.planner import LLMRemediationPlanningService

# In _build_live_dependencies(settings, dispatcher):
remediation_service = LLMRemediationPlanningService(reasoning_provider=provider)
# Pass to GraphDependencies(..., remediation_planning_service=remediation_service)
```

### 4.3 Graph Test Fixtures
```python
from tests.support.scripted_remediation_planner import ScriptedRemediationPlanningService

# In graph and runner unit tests:
scripted_remediation = ScriptedRemediationPlanningService()
```
