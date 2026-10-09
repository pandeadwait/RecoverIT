# Person 3 Workstream Handoff

## 1. Overview and Scope

This handoff documents Person 3's deliverables for RecoverIT on the `feature/evidence-reasoning` branch.
Person 3 is responsible for evidence normalization & context snapshot building, timeline temporal reasoning, missing information assessment, capability-aware query planning, hypothesis generation & revision, deterministic stopping rule evaluation, and final ranking.

All implementations strictly satisfy the frozen LangGraph port interfaces defined in `investigation/graph/ports.py` without modifying frozen contracts, graph topology, runner, or collector code.

---

## 2. Commit Log and Milestone History

- **Milestone 1**: `9d7ed05` - `feat(evidence): implement DefaultContextBuilder satisfying frozen graph protocol and canonical coverage`
- **Milestone 2**: `6eb834b` - `feat(investigation): conform MissingInformationService and QueryPlanningService to frozen graph protocols`
- **Milestone 3**: `61ba5bc` - `feat(reasoning): implement HypothesisService, StoppingService, RankingEngine and decouple test scenarios`
- **Milestone 4**: `docs(handoff): add Person 3 handoff documentation and contract change review`

### Final Commit
- Branch: `feature/evidence-reasoning`
- Head: `feature/evidence-reasoning` (branched from `origin/langgraph-interface-v1`)

---

## 3. Changed, Added, and Deleted Files

### Added Files
- `evidence/context/builder.py` (updated to export `DefaultContextBuilder`)
- `reasoning/hypotheses/service.py`: Implements `DefaultHypothesisService` satisfying `HypothesisService`.
- `reasoning/stopping.py`: Implements `DefaultStoppingService` (and `StoppingRuleEvaluator`) satisfying `StoppingService`.
- `tests/evidence/test_context_builder.py`: Unit tests verifying `DefaultContextBuilder` against canonical schemas, secret redaction, timeline sorting, temporal relationships, and 7-source coverage.
- `tests/reasoning/test_stopping_service.py`: Unit tests verifying all stopping rules, priority ordering, and canonical `StopDecision` outputs.
- `tests/reasoning/test_unseen_scenarios.py`: Verifies hypothesis generation, query planning, missing info assessment, stopping, and ranking on unseen domain incidents without hardcoded scenario dependencies.
- `tests/support/scripted_reasoning_provider.py`: Generic `ScriptedReasoningProvider` for unseen incidents, plus compatibility `FakeReasoningProvider` for legacy test presets.
- `docs/handoffs/PERSON_3_HANDOFF.md`: This handoff document.

### Modified Files
- `evidence/__init__.py`: Exports `DefaultContextBuilder`.
- `evidence/context/__init__.py`: Exports `ContextBuilder` and `DefaultContextBuilder`.
- `evidence/context/builder.py`: Implements `DefaultContextBuilder` adhering to `ContextBuilder` protocol.
- `evidence/context/coverage.py`: Supports both legacy 6 source types (for backward-compatible context snapshots) and canonical 7 source types (including health) for graph snapshots.
- `investigation/missing_information/assessor.py`: Implements `MissingInformationService` protocol with backward-compatible keyword arguments and generic fallback.
- `investigation/query_planning/planner.py`: Implements `QueryPlanningService` protocol with capability awareness, budget constraints, and history deduplication.
- `reasoning/__init__.py`: Exports `DefaultHypothesisService`, `DefaultStoppingService`, `ExtendedStopDecision`, and `StoppingRuleEvaluator`.
- `reasoning/hypotheses/__init__.py`: Exports `DefaultHypothesisService`.
- `reasoning/hypotheses/reviser.py`: Increments hypothesis set revision monotonically and validates citations against updated context.
- `reasoning/provider/fake_provider.py`: Lightweight shim redirecting to `tests/support/scripted_reasoning_provider.py` to decouple test presets from production reasoning.
- `reasoning/ranking/ranking_engine.py`: Implements `RankingService` protocol accepting canonical parameters and producing `RankedHypothesisSet`.
- `docs/contract-change-requests/PERSON_3.md`: Recorded confirmation that frozen contracts were fully sufficient.

### Deleted Files
- None.

---

## 4. Frozen Protocol Conformance

All 6 frozen graph protocols from `investigation/graph/ports.py` are fully implemented:

| Protocol Port | Implementing Class | File Location | Conformance Check |
|---|---|---|---|
| `ContextBuilder` | `DefaultContextBuilder` | `evidence/context/builder.py` | `isinstance(builder, ContextBuilder) == True` |
| `MissingInformationService` | `MissingInformationAssessor` | `investigation/missing_information/assessor.py` | `isinstance(service, MissingInformationService) == True` |
| `QueryPlanningService` | `EvidenceQueryPlanner` | `investigation/query_planning/planner.py` | `isinstance(service, QueryPlanningService) == True` |
| `HypothesisService` | `DefaultHypothesisService` | `reasoning/hypotheses/service.py` | `isinstance(service, HypothesisService) == True` |
| `StoppingService` | `DefaultStoppingService` | `reasoning/stopping.py` | `isinstance(service, StoppingService) == True` |
| `RankingService` | `RankingEngine` | `reasoning/ranking/ranking_engine.py` | `isinstance(service, RankingService) == True` |

---

## 5. Preservation of Core System Invariants

1. **Evidence Normalization & Provenance**: `DefaultContextBuilder` normalizes raw batches, builds `EvidenceSummaryProjection` records with valid `EvidenceType`, sets `Reliability`, hashes, and preserves query/batch provenance.
2. **Security & Redaction**: Evaluates payloads through `Redactor`. Quarantines private key material, scrubs passwords and tokens, marks `redactions_applied=True`, and records quarantine warnings.
3. **Deterministic Timeline & Temporal Relationships**: Chronologically sorts all events, detects `PRECEDES` vs `COINCIDES_WITH`, generates non-negative `delta_ms`, and assigns deterministic IDs.
4. **Citation Validation**: `CitationValidator` ensures every supporting and contradicting citation references valid evidence IDs in the context snapshot with causal roles (`cause`, `effect`, `correlation`).
5. **Support and Contradiction Separation**: Hypotheses strictly distinguish supporting from contradicting citations; ranking engine applies penalties for contradictions.
6. **Capability-Aware Query Planning**: `EvidenceQueryPlanner` filters requested queries against `SourceCapabilityCatalog`, enforces allowed query fields, window limits, and budget caps.
7. **Bounded Stopping**: Evaluates budget exhaustion (rounds, queries, reasoning calls), stopping criteria completion, and source unavailability deterministically.
8. **Deterministic Ranking & Tie Breaking**: Computes 8-factor score breakdown (0-100), assigns confidence bands, breaks ties deterministically by `hypothesis_id`, and explicitly marks inconclusive investigations.
9. **Unseen Incident Generalization**: All scenario-specific strings (`incident_001`, `incident_002`, `scenario_...`) have been removed from production reasoning code.

---

## 6. Test Commands and Results

### Full Test Suite Execution
```powershell
.venv\Scripts\python -m pytest -q
```
**Result**:
```text
610 passed, 1 warning, 40 subtests passed in 3.57s
```

### Workstream Test Suites
```powershell
.venv\Scripts\python -m pytest tests/evidence tests/reasoning tests/investigation tests/graph -v
```
**Result**:
- `tests/evidence`: 4 passed (100%)
- `tests/reasoning`: 71 passed (100%)
- `tests/investigation`: 133 passed (100%)
- `tests/graph`: 6 passed (100%)
- **Total**: 214 passed across workstream & graph integration tests.

---

## 7. Dependencies and Environment Variables

### Required Dependencies
- Python 3.12
- `pydantic>=2.0`
- `langgraph>=1.2.14,<1.3`
- `langgraph-checkpoint-sqlite>=3.1.1,<3.2`
- `pytest>=8.0`

### Optional Environment Variables (for Live LLM Provider)
- `OPENAI_API_KEY`: API key for OpenAI GPT-4o / reasoning models.
- `ANTHROPIC_API_KEY`: API key for Anthropic Claude 3.5 Sonnet.
- `GEMINI_API_KEY`: API key for Google Gemini models.
- `RECOVERIT_ENV`: Environment identifier (`test`, `production`).

---

## 8. Contract-Change Requests

**No contract change requests were filed.**
All frozen contracts in `contracts/` and protocol signatures in `investigation/graph/ports.py` were sufficient. See [PERSON_3.md](file:///d:/PJT_1/docs/contract-change-requests/PERSON_3.md).

---

## 9. Known Limitations

1. **Offline Mode Heuristic Diversity**: When operating without live LLM credentials, hypothesis generation relies on generic structural failure archetypes (deployment regression, configuration drift, external dependency degradation, resource exhaustion) rather than deep semantic language modeling.
2. **Context Window Scaling**: For very large incidents with >10,000 evidence records, context snapshots project compact summaries (`EvidenceSummaryProjection`) while full raw records remain accessible via `IncidentContextReader`.

---

## 10. Example Runtime Composition

```python
from datetime import datetime, timezone
from contracts.incident.schemas import IncidentSeed
from contracts.common import Severity, SourceType
from evidence.context.builder import DefaultContextBuilder
from investigation.missing_information.assessor import MissingInformationAssessor
from investigation.query_planning.planner import EvidenceQueryPlanner
from reasoning.hypotheses.service import DefaultHypothesisService
from reasoning.stopping import DefaultStoppingService
from reasoning.ranking.ranking_engine import RankingEngine
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.builder import build_investigation_graph

# 1. Instantiate Person 3 services
context_builder = DefaultContextBuilder()
missing_info_service = MissingInformationAssessor()
query_planner = EvidenceQueryPlanner()
hypothesis_service = DefaultHypothesisService()
stopping_service = DefaultStoppingService(
    min_supporting_sources_for_adequate=2,
    min_evidence_score_for_confidence=50.0,
)
ranking_service = RankingEngine()

# 2. Assemble GraphDependencies for LangGraph orchestration
deps = GraphDependencies(
    collection_service=collection_service,  # Person 1
    context_builder=context_builder,        # Person 3
    missing_information_service=missing_info_service,  # Person 3
    query_planning_service=query_planner,   # Person 3
    hypothesis_service=hypothesis_service,  # Person 3
    stopping_service=stopping_service,      # Person 3
    ranking_service=ranking_service,        # Person 3
    progress_sink=progress_sink,
    clock=clock,
)

# 3. Build runnable LangGraph investigation workflow
workflow = build_investigation_graph(deps)
app = workflow.compile()
```
