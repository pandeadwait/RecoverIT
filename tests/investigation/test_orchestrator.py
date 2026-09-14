"""
Unit and integration tests for InvestigationOrchestrator and StoppingRuleEvaluator.

Verifies:
- Full loop with fake provider + in-memory stubs completes successfully.
- Loop stops when budget is exhausted.
- Loop stops when evidence is sufficient.
- Loop handles inconclusive correctly across failure modes.
- State transitions strictly follow the state machine and monotonic versions.
- Checkpoints are created before and after external calls.
- Each stopping rule is individually testable.
- Boundary rule: No remediation or action execution path exists.

See WORK_DIVISION.md §8.8, §8.9 and ARCHITECTURE.md §9.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
import pytest

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InformationPriority,
    InformationValueLevel,
    InvestigationState,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    EvidenceRole,
    Hypothesis,
    HypothesisSet,
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.budgets.budget_tracker import BudgetTracker
from investigation.orchestration.orchestrator import (
    CollectionService,
    ContextBuilder,
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
    StoppingDecision,
    StoppingRuleEvaluator,
)
from investigation.orchestration.state_machine import (
    InMemoryCheckpointStore,
    LEGAL_TRANSITIONS,
)
from reasoning.provider.fake_provider import FakeReasoningProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_orch_01",
        external_alert_id="alt_ext_201",
        service="checkout-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="HTTP 500 error spike on /checkout endpoint",
        labels={"tier": "1", "team": "checkout"},
    )


@pytest.fixture
def sample_catalog(sample_incident: IncidentSeed) -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        sources=[
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit"],
                maximum_window_seconds=3600,
                maximum_items=500,
            ),
            SourceCapability(
                source_type=SourceType.DEPLOYMENTS,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_window_seconds=86400,
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=True,
                supported_query_fields=["metric", "service", "limit"],
                maximum_window_seconds=3600,
                maximum_items=200,
            ),
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    e1 = EvidenceSummaryProjection(
        evidence_id="ev_deploy_01",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=datetime(2026, 9, 12, 9, 55, 0, tzinfo=timezone.utc),
        summary="Deployment v1.8.0 updated checkout config",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e2 = EvidenceSummaryProjection(
        evidence_id="ev_log_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 2, 0, tzinfo=timezone.utc),
        summary="Connection timeout to payments database",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    return IncidentContextSnapshot(
        snapshot_id="ctx_orch_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 5, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        evidence=[e1, e2],
    )


# ---------------------------------------------------------------------------
# Full Loop Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_loop_completes_successfully(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Verify that the complete investigation loop runs and completes with ranked output."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder(synthetic_evidence=sample_context.evidence)
    checkpoint_store = InMemoryCheckpointStore()
    progress_events: list[dict[str, object]] = []

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
        checkpoint_store=checkpoint_store,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
        progress_callback=progress_events.append,
    )

    budget = InvestigationBudget(max_rounds=2, max_queries=5, max_reasoning_calls=10)
    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        budget=budget,
    )

    # Output contract assertions
    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert result.stop_reason is None
    assert len(result.hypotheses) >= 1
    assert result.hypotheses[0].rank == 1
    assert result.hypotheses[0].evidence_score > 0.0
    assert result.hypotheses[0].confidence_label in {
        ConfidenceLabel.HIGH,
        ConfidenceLabel.MEDIUM,
        ConfidenceLabel.LOW,
    }

    # Budget assertions
    assert result.budget_usage.rounds >= 1
    assert result.budget_usage.queries >= 1
    assert result.budget_usage.reasoning_calls >= 1

    # State machine assertions
    sm = orchestrator.state_machine
    assert sm is not None
    assert sm.current_state == InvestigationState.COMPLETED
    assert sm.is_terminal

    # Checkpoint assertions
    checkpoints = checkpoint_store.list_checkpoints(sample_incident.incident_id)
    assert len(checkpoints) >= 4
    labels = [cp.label for cp in checkpoints]
    assert any("pre_call:MissingInformationAssessor.assess" in lbl for lbl in labels)
    assert any("post_call:MissingInformationAssessor.assess" in lbl for lbl in labels)
    assert any("pre_call:CollectionService.execute" in lbl for lbl in labels)
    assert any("post_call:CollectionService.execute" in lbl for lbl in labels)

    # CLI-facing trace assertions: real rationale, tool calls, and outputs are emitted.
    kinds = {event["kind"] for event in progress_events}
    assert {"assessment", "reasoning", "action", "observation", "decision"} <= kinds
    tool_results = [event for event in progress_events if event["kind"] == "observation"]
    assert any(event.get("output") for event in tool_results)
    assert any(
        isinstance(event.get("metadata"), dict)
        and event["metadata"].get("record_count", 0) > 0
        for event in tool_results
    )


@pytest.mark.asyncio
async def test_state_transitions_strictly_adhere_to_state_machine(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Verify that every state transition in the loop is legal and strictly increments state_version."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder(synthetic_evidence=sample_context.evidence)

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
    )

    sm = orchestrator.state_machine
    assert sm is not None
    history = sm.history

    # Verify transition sequence
    states = [t.to_state for t in history]
    assert states[0] == InvestigationState.ASSESSING_GAPS
    assert InvestigationState.COLLECTING_EVIDENCE in states
    assert InvestigationState.BUILDING_TIMELINE in states
    assert InvestigationState.GENERATING_HYPOTHESES in states
    assert InvestigationState.RANKING in states
    assert states[-1] == InvestigationState.COMPLETED

    # Verify each step is strictly legal in state machine rules
    for t in history:
        allowed = LEGAL_TRANSITIONS[t.from_state]
        assert t.to_state in allowed, f"Illegal transition from {t.from_state} to {t.to_state}"

    # Verify strictly monotonic versions
    versions = [t.state_version for t in history]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


# ---------------------------------------------------------------------------
# Stopping Rules & Budget Exhaustion Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_stops_when_budget_exhausted(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Verify that the orchestrator terminates when the budget is reached."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder(synthetic_evidence=sample_context.evidence)

    # Budget allows exactly 1 round
    tight_budget = InvestigationBudget(max_rounds=1, max_queries=5)
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=99),
    )

    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        budget=tight_budget,
    )

    assert orchestrator.state_machine.current_state == InvestigationState.COMPLETED
    assert result.budget_usage.rounds == 1


@pytest.mark.asyncio
async def test_loop_stops_when_evidence_is_sufficient(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Verify loop stops early in round 1 if evidence coverage is sufficient."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder(synthetic_evidence=sample_context.evidence)

    # Evaluator requires only 1 supporting source for adequate coverage
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    large_budget = InvestigationBudget(max_rounds=5, max_queries=20)
    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        budget=large_budget,
    )

    # Stopped on round 1 because evidence is already sufficient
    assert result.status == InvestigationStatus.COMPLETED
    assert result.budget_usage.rounds == 1


# ---------------------------------------------------------------------------
# Inconclusive Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_handles_inconclusive_when_sources_unavailable(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
) -> None:
    """When operational data sources are unavailable, orchestrator returns inconclusive."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    # All queries return UNAVAILABLE status
    collection_service = InMemoryCollectionService(default_status=SourceStatus.UNAVAILABLE)
    context_builder = InMemoryContextBuilder(synthetic_evidence=[])

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
    )

    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
    )

    assert result.status == InvestigationStatus.INCONCLUSIVE
    assert result.stop_reason == StopReason.SOURCES_UNAVAILABLE
    assert orchestrator.state_machine.current_state == InvestigationState.INCONCLUSIVE
    assert result.hypotheses == []


@pytest.mark.asyncio
async def test_loop_handles_inconclusive_when_no_valid_evidence_collected(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
) -> None:
    """When no valid evidence could be collected or cited, result is inconclusive."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService(responses={"qry_deploy_01": []})
    context_builder = InMemoryContextBuilder(synthetic_evidence=[])

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
    )

    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
    )

    assert result.status == InvestigationStatus.INCONCLUSIVE
    assert result.stop_reason in {
        StopReason.INSUFFICIENT_EVIDENCE,
        StopReason.SOURCES_UNAVAILABLE,
    }
    assert orchestrator.state_machine.current_state == InvestigationState.INCONCLUSIVE


# ---------------------------------------------------------------------------
# Individual Stopping Rule Unit Tests
# ---------------------------------------------------------------------------


def test_stopping_evaluator_budget_exhausted_with_evidence(
    sample_context: IncidentContextSnapshot,
) -> None:
    evaluator = StoppingRuleEvaluator()
    tracker = BudgetTracker(budget=InvestigationBudget(max_rounds=1))
    tracker.record_round()  # Exhaust rounds limit

    hyp_set = HypothesisSet(
        incident_id="inc_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_01",
                statement="Statement",
                root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                affected_component="svc",
                supporting_evidence=[EvidenceCitation(evidence_id="ev_deploy_01", reason="r")],
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=sample_context,
        hypotheses=hyp_set,
        budget_tracker=tracker,
    )

    assert decision.should_stop
    assert not decision.is_inconclusive
    assert decision.stop_reason == StopReason.BUDGET_EXHAUSTED


def test_stopping_evaluator_budget_exhausted_without_evidence(
    sample_context: IncidentContextSnapshot,
) -> None:
    evaluator = StoppingRuleEvaluator()
    tracker = BudgetTracker(budget=InvestigationBudget(max_rounds=1))
    tracker.record_round()

    # Hypotheses without valid citations
    hyp_set = HypothesisSet(
        incident_id="inc_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_01",
                statement="Statement",
                root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                affected_component="svc",
                supporting_evidence=[EvidenceCitation(evidence_id="nonexistent_id", reason="r")],
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=sample_context,
        hypotheses=hyp_set,
        budget_tracker=tracker,
    )

    assert decision.should_stop
    assert decision.is_inconclusive
    assert decision.stop_reason == StopReason.BUDGET_EXHAUSTED


def test_stopping_evaluator_query_plan_sources_unavailable(
    sample_context: IncidentContextSnapshot,
) -> None:
    evaluator = StoppingRuleEvaluator()
    plan = EvidenceQueryPlan(
        incident_id="inc_01",
        plan_id="plan_01",
        round=1,
        queries=[],
        stop_reason=StopReason.SOURCES_UNAVAILABLE,
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=sample_context,
        hypotheses=None,
        query_plan=plan,
    )

    assert decision.should_stop
    assert decision.is_inconclusive
    assert decision.stop_reason == StopReason.SOURCES_UNAVAILABLE


def test_stopping_evaluator_adequate_coverage(
    sample_context: IncidentContextSnapshot,
) -> None:
    # 2 distinct sources: DEPLOYMENTS (ev_deploy_01) and LOGS (ev_log_01)
    hyp_set = HypothesisSet(
        incident_id="inc_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_01",
                statement="Statement",
                root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                affected_component="svc",
                supporting_evidence=[
                    EvidenceCitation(evidence_id="ev_deploy_01", reason="r1", role=EvidenceRole.CAUSE),
                    EvidenceCitation(evidence_id="ev_log_01", reason="r2", role=EvidenceRole.EFFECT),
                ],
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_01",
                statement="Alternative statement",
                root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
                affected_component="svc",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    evaluator = StoppingRuleEvaluator(min_supporting_sources_for_adequate=2)
    decision = evaluator.evaluate(
        round_num=1,
        context=sample_context,
        hypotheses=hyp_set,
    )

    assert decision.should_stop
    assert not decision.is_inconclusive
    assert decision.stop_reason == StopReason.SUFFICIENT_EVIDENCE


def test_stopping_evaluator_high_value_questions_resolved(
    sample_context: IncidentContextSnapshot,
) -> None:
    hyp_set = HypothesisSet(
        incident_id="inc_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_01",
                statement="Statement",
                root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                affected_component="svc",
                supporting_evidence=[EvidenceCitation(evidence_id="ev_deploy_01", reason="r1")],
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    # Assessment with no HIGH priority gaps and >= 2 known facts
    assessment = MissingInformationAssessment(
        incident_id="inc_01",
        assessment_id="ass_01",
        generated_at=datetime.now(timezone.utc),
        known_facts=[
            KnownFact(statement="Deployment happened", supporting_evidence_ids=["ev_deploy_01"]),
            KnownFact(statement="Error logged", supporting_evidence_ids=["ev_log_01"]),
        ],
        missing_information=[
            MissingInformationItem(
                information_id="gap_low",
                question="Minor question",
                reason="Minor question regarding logs",
                priority=InformationPriority.LOW,
                candidate_sources=[SourceType.LOGS],
            )
        ],
    )

    evaluator = StoppingRuleEvaluator(min_supporting_sources_for_adequate=5)
    decision = evaluator.evaluate(
        round_num=1,
        context=sample_context,
        hypotheses=hyp_set,
        missing_info=assessment,
    )

    assert decision.should_stop
    assert not decision.is_inconclusive
    assert decision.stop_reason == StopReason.SUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# Project Boundary Enforcement Tests
# ---------------------------------------------------------------------------


def test_no_remediation_or_execution_path_exists() -> None:
    """
    Verify Person 3 architectural boundary:
    InvestigationOrchestrator must NEVER contain remediation, self-healing,
    or action execution methods.
    """
    forbidden_terms = [
        "remediate",
        "remediation",
        "heal",
        "self_heal",
        "rollback",
        "restart",
        "execute_action",
        "apply_fix",
        "patch",
        "deploy_fix",
    ]

    method_names = [
        name for name, _ in inspect.getmembers(InvestigationOrchestrator, predicate=inspect.isroutine)
    ]

    for term in forbidden_terms:
        matches = [m for m in method_names if term in m.lower()]
        assert (
            not matches
        ), f"Boundary violation: Found forbidden remediation/action method(s): {matches}"

    # Verify return type of run() is RankedHypothesisSet
    sig = inspect.signature(InvestigationOrchestrator.run)
    assert sig.return_annotation in {RankedHypothesisSet, "RankedHypothesisSet"}
