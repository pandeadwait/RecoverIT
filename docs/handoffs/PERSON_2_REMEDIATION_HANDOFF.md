# Person 2 Handoff: LangGraph Orchestration Wiring, Runtime Composition, & Presentation

## 1. Branch & Commit Information

- **Role**: Person 2 (Orchestration & Presentation)
- **Branch**: `code-generation-person-2`
- **Base Branch**: `origin/cleanup`
- **Milestone Commit SHA**: `b8bc1fb47c754dad866c9d8be14be2d677c2c7de` (Implementation) / `74b45ff` (Handoff Documentation)

---

## 2. File Ownership & Changes Breakdown

All changes strictly adhere to the ownership matrix specified in `REMEDIATION_TWO_PERSON_EXECUTION_PLAN.md` §2.1. Zero prohibited files (`contracts/remediation/**`, `remediation/**`, `reasoning/provider/**`, `collectors/**`, `docs/handoffs/PERSON_1_REMEDIATION_HANDOFF.md`) were modified.

### 2.1 Added Files (7)
| Path | Component | Purpose |
|---|---|---|
| `investigation/graph/nodes/remediate.py` | LangGraph Node | Implements `plan_remediation` node that invokes `dependencies.remediation_planning_service.plan(ranked, context)` and publishes a display-safe `ProgressEvent`. |
| `recoverit/web/static/index.html` | Web Presentation | HTML layout container with `#remediation-section`, prominent safety banners, metadata grid, and step cards without automated execution buttons. |
| `recoverit/web/static/style.css` | Web Presentation | Clean styling for remediation panels, operational risk badges (`LOW`, `MEDIUM`, `HIGH`, `BLOCKED`), collapsible verification/rollback details, and blocked notices. |
| `recoverit/web/static/app.js` | Web Presentation | Client-side dynamic rendering for investigation results and remediation guidance, with collapsible success verification checks and safe rollback procedures. |
| `tests/graph/test_remediation_node.py` | Unit Tests | Unit tests for `plan_remediation` node verifying protocol compliance, progress event emission, blocked plan handling, and secret scrubbing. |
| `tests/graph/test_investigation_graph_remediation.py` | Integration Tests | Integration tests validating LangGraph topology, routing for completed and inconclusive investigations, and checkpoint resume persistence. |
| `tests/integration/test_remediation_runner.py` | End-to-End Tests | End-to-end live runner, CLI Rich display, and web API / static asset tests. |

### 2.2 Modified Files (9)
| Path | Component | Changes Made |
|---|---|---|
| `investigation/graph/ports.py` | Graph Boundary | Added `@runtime_checkable class RemediationPlanningService(Protocol)` with `async def plan(...) -> RemediationPlan`. |
| `investigation/graph/state.py` | Graph State | Added `remediation_plan: RemediationPlan | None` to `InvestigationGraphState` and `InvestigationOutput`. |
| `investigation/graph/dependencies.py` | Dependencies | Added `remediation_planning_service: RemediationPlanningService | None = None` to `GraphDependencies`. |
| `investigation/graph/nodes/__init__.py` | Node Exports | Re-exported `plan_remediation` node in `__all__`. |
| `investigation/graph/builder.py` | Graph Builder | Registered `"plan_remediation"` in `NODE_NAMES`; wired edges: `rank_hypotheses -> plan_remediation -> END` and `finish_inconclusive -> plan_remediation -> END`. |
| `recoverit/composition.py` | Runtime Composition | Wired `remediation_planning_service` into `build_runtime` and dynamically instantiated `LLMRemediationPlanningService(reasoning_provider=provider)` in `_build_live_dependencies`. |
| `recoverit/runner.py` | Runner & DTO | Added `remediation_plan: dict[str, Any] | None = None` to `InvestigationResult`, extracted graph output in `_to_result`, and formatted `## Suggested Remediation — Human Review Required` in `to_markdown_report()`. |
| `recoverit/cli.py` | Terminal Presentation | Added Rich formatted panels, operational risk badges, prerequisite checklists, numbered step cards, and blocked guidance panels to `display_results()`. |
| `recoverit/web/configured.py` | Web API | Mounted `/static` directory for UI assets, added `GET /api/investigations/{incident_id}` route returning `result_payload` with `remediation_plan`. |

---

## 3. Integration Interface Verification

The decoupling interface between Person 1 and Person 2 is strictly verified:

1. **Protocol (`investigation/graph/ports.py`)**:
   ```python
   @runtime_checkable
   class RemediationPlanningService(Protocol):
       async def plan(
           self,
           ranked: RankedHypothesisSet,
           context: IncidentContextSnapshot,
       ) -> RemediationPlan: ...
   ```
2. **State & Output (`investigation/graph/state.py`)**:
   ```python
   remediation_plan: RemediationPlan | None
   ```
3. **Graph Builder Routing (`investigation/graph/builder.py`)**:
   ```python
   builder.add_node("plan_remediation", partial(plan_remediation, dependencies=dependencies))
   builder.add_edge("rank_hypotheses", "plan_remediation")
   builder.add_edge("finish_inconclusive", "plan_remediation")
   builder.add_edge("plan_remediation", END)
   ```
4. **Live Composition (`recoverit/composition.py`)**:
   ```python
   remediation_service = LLMRemediationPlanningService(reasoning_provider=provider)
   GraphDependencies(..., remediation_planning_service=remediation_service)
   ```
When Person 1's branch is merged into `cleanup`, the code links directly with zero glue code or adapter wrappers.

---

## 4. Test Commands and Execution Results

All tests pass cleanly with 100% success rate:

### 4.1 Unit Tests: `plan_remediation` Node
```bash
.\.venv\Scripts\python.exe -m pytest tests/graph/test_remediation_node.py -v
```
**Output**:
```text
tests/graph/test_remediation_node.py::test_remediation_service_conforms_to_protocol PASSED [ 20%]
tests/graph/test_remediation_node.py::test_plan_remediation_invokes_service_and_emits_event PASSED [ 40%]
tests/graph/test_remediation_node.py::test_plan_remediation_handles_blocked_plan PASSED [ 60%]
tests/graph/test_remediation_node.py::test_plan_remediation_progress_events_scrub_raw_credentials PASSED [ 80%]
tests/graph/test_remediation_node.py::test_plan_remediation_handles_none_service_gracefully PASSED [100%]
===== 5 passed in 0.83s =====
```

### 4.2 Integration Tests: LangGraph Routing & Checkpoints
```bash
.\.venv\Scripts\python.exe -m pytest tests/graph/test_investigation_graph_remediation.py -v
```
**Output**:
```text
tests/graph/test_investigation_graph_remediation.py::test_remediation_node_in_graph_topology PASSED [ 25%]
tests/graph/test_investigation_graph_remediation.py::test_completed_investigation_routes_to_plan_remediation PASSED [ 50%]
tests/graph/test_investigation_graph_remediation.py::test_inconclusive_investigation_routes_to_plan_remediation PASSED [ 75%]
tests/graph/test_investigation_graph_remediation.py::test_checkpoint_preserves_remediation_plan_on_resume PASSED [100%]
===== 4 passed in 0.86s =====
```

### 4.3 End-to-End Tests: Runner, CLI & Web Presentation
```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_remediation_runner.py -v
```
**Output**:
```text
tests/integration/test_remediation_runner.py::test_runner_produces_result_with_remediation_plan PASSED [ 20%]
tests/integration/test_remediation_runner.py::test_markdown_report_renders_available_recommendation PASSED [ 40%]
tests/integration/test_remediation_runner.py::test_markdown_report_renders_blocked_recommendation PASSED [ 60%]
tests/integration/test_remediation_runner.py::test_cli_display_results_renders_remediation_panel PASSED [ 80%]
tests/integration/test_remediation_runner.py::test_web_configured_serves_remediation_and_static_assets PASSED [100%]
===== 5 passed, 1 warning in 1.22s =====
```

### 4.4 Full Regression Suite (Repository-Wide)
```bash
.\.venv\Scripts\python.exe -m pytest -q
```
**Output**:
```text
485 passed, 1 warning, 40 subtests passed in 21.34s
```
Zero regressions across the entire codebase.

---

## 5. Presentation & Safety Highlights

1. **Safety Notice & Risk Badges**:
   - Both CLI and Web UI display prominent human operator review banners.
   - Distinct operational risk tier badges: `LOW` (green), `MEDIUM` (blue), `HIGH` (amber), `BLOCKED` (red).
2. **Actionable Step Cards**:
   - Each recommended step displays purpose, expected result, numbered guidance, and mandatory human sign-off indicator.
   - Verification checks and safe rollback instructions are organized into collapsible accordions.
3. **Conservative Inconclusive / Blocked Fallback**:
   - If `recommendation_available == False`, changes are prohibited, prominently displaying: `> **No production change recommended.**` alongside escalation paths and unresolved uncertainties.
4. **Execution Prevention**:
   - Web frontend and CLI are strictly recommendation-only; no buttons exist for automated execution (`Apply`, `Run`, or `Rollback`).
