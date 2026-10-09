"""Tests for DefaultStoppingService and deterministic stopping rules."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.common import (
    EvidenceRole,
    EvidenceType,
    InformationPriority,
    RootCauseCategory,
    SourceCoverageStatus,
    SourceType,
    StopAction,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
    Reliability,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
)
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
    MissingInformationItem,
    StopDecision,
)
from investigation.graph.ports import StoppingService
from reasoning.stopping import DefaultStoppingService, ExtendedStopDecision, StoppingRuleEvaluator


NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_context(evidence: list[EvidenceSummaryProjection] | None = None) -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        snapshot_id="ctx-test-1",
        incident_id="inc-test-1",
        revision=1,
        created_at=NOW,
        incident=IncidentSummary(
            service="order-service",
            environment="production",
            severity="critical",
            detected_at=NOW,
            summary="Payment processing failures",
        ),
        evidence=evidence or [],
        source_coverage={
            source.value: SourceCoverageStatus.AVAILABLE for source in SourceType
        },
    )


def _make_evidence_projection(
    evidence_id: str,
    source_type: SourceType,
    summary: str = "Test evidence",
    evidence_type: EvidenceType = EvidenceType.LOG_EVENT,
) -> EvidenceSummaryProjection:
    return EvidenceSummaryProjection(
        evidence_id=evidence_id,
        source_type=source_type,
        evidence_type=evidence_type,
        event_time=NOW,
        summary=summary,
        quality=EvidenceQuality(
            reliability=Reliability.HIGH,
            freshness_seconds=0,
            redactions_applied=False,
        ),
    )


class TestDefaultStoppingService:
    """Validates Person 3's stopping service implementation."""

    def test_implements_frozen_protocol(self) -> None:
        service = DefaultStoppingService()
        assert isinstance(service, StoppingService)
        # Compatibility alias
        assert StoppingRuleEvaluator is DefaultStoppingService

    def test_zero_hypotheses_stops_inconclusive(self) -> None:
        service = DefaultStoppingService()
        context = _make_context([_make_evidence_projection("ev-1", SourceType.LOGS)])
        hypotheses = HypothesisSet(
            incident_id="inc-test-1",
            revision=1,
            generated_at=NOW,
            hypotheses=[],
        )

        decision = service.evaluate(
            context=context,
            hypotheses=hypotheses,
            round_number=1,
        )

        assert isinstance(decision, StopDecision)
        assert isinstance(decision, ExtendedStopDecision)
        assert decision.action == StopAction.INCONCLUSIVE
        assert decision.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
        assert decision.should_stop is True
        assert decision.is_inconclusive is True

    def test_zero_evidence_continues_when_budget_permits(self) -> None:
        service = DefaultStoppingService()
        context = _make_context(evidence=[])

        class DummyPlan:
            queries = ["query-1"]
            stop_reason = None

        decision = service.evaluate(
            context=context,
            query_plan=DummyPlan(),
            budget=InvestigationBudget(max_rounds=3),
            round_number=1,
        )

        assert decision.action == StopAction.CONTINUE
        assert decision.should_stop is False

    def test_zero_evidence_stops_inconclusive_when_budget_exhausted(self) -> None:
        service = DefaultStoppingService()
        context = _make_context(evidence=[])

        decision = service.evaluate(
            context=context,
            budget=InvestigationBudget(max_rounds=3),
            round_number=3,
        )

        assert decision.action == StopAction.INCONCLUSIVE
        assert decision.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
        assert decision.should_stop is True

    def test_all_completion_criteria_met_stops_to_rank(self) -> None:
        service = DefaultStoppingService(
            min_supporting_sources_for_adequate=2,
            min_evidence_score_for_confidence=40.0,
            min_score_margin=5.0,
        )
        ev1 = _make_evidence_projection(
            "ev-deploy-1",
            SourceType.DEPLOYMENTS,
            "Deploy v1.2",
            evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        )
        ev2 = _make_evidence_projection(
            "ev-log-1",
            SourceType.LOGS,
            "Error 500 in logs",
            evidence_type=EvidenceType.LOG_EVENT,
        )
        context = _make_context([ev1, ev2])

        h1 = Hypothesis(
            hypothesis_id="hyp-1",
            incident_id="inc-test-1",
            statement="Deploy v1.2 introduced memory leak",
            root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
            affected_component="order-service",
            supporting_evidence=[
                EvidenceCitation(
                    evidence_id="ev-deploy-1",
                    reason="Deployment introduced bug",
                    role=EvidenceRole.CAUSE,
                ),
                EvidenceCitation(
                    evidence_id="ev-log-1",
                    reason="Logs show errors after deploy",
                    role=EvidenceRole.EFFECT,
                ),
            ],
        )
        h2 = Hypothesis(
            hypothesis_id="hyp-2",
            incident_id="inc-test-1",
            statement="Network degradation causing partition",
            root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
            affected_component="order-service",
            supporting_evidence=[],
        )
        hypotheses = HypothesisSet(
            incident_id="inc-test-1",
            revision=1,
            generated_at=NOW,
            hypotheses=[h1, h2],
        )

        decision = service.evaluate(
            context=context,
            hypotheses=hypotheses,
            round_number=1,
        )

        assert decision.action == StopAction.RANK
        assert decision.stop_reason == StopReason.SUFFICIENT_EVIDENCE
        assert decision.should_stop is True
        assert decision.is_inconclusive is False
        assert len(decision.unresolved_criteria) == 0

    def test_budget_exhaustion_stops_inconclusive_with_unresolved_criteria(self) -> None:
        service = DefaultStoppingService(
            min_supporting_sources_for_adequate=2,
            min_evidence_score_for_confidence=80.0,
        )
        # Only 1 source present, so completion criteria fail
        ev1 = _make_evidence_projection("ev-log-1", SourceType.LOGS, "Error 500")
        context = _make_context([ev1])

        h1 = Hypothesis(
            hypothesis_id="hyp-1",
            incident_id="inc-test-1",
            statement="Database lock contention on orders table",
            root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
            affected_component="order-service",
            supporting_evidence=[
                EvidenceCitation(
                    evidence_id="ev-log-1",
                    reason="Errors in log",
                    role=EvidenceRole.EFFECT,
                )
            ],
        )
        hypotheses = HypothesisSet(
            incident_id="inc-test-1",
            revision=3,
            generated_at=NOW,
            hypotheses=[h1],
        )

        decision = service.evaluate(
            context=context,
            hypotheses=hypotheses,
            budget=InvestigationBudget(max_rounds=3),
            round_number=3,
        )

        assert decision.action == StopAction.INCONCLUSIVE
        assert decision.stop_reason == StopReason.BUDGET_EXHAUSTED
        assert decision.should_stop is True
        assert len(decision.unresolved_criteria) > 0

    def test_sources_unavailable_stops_inconclusive(self) -> None:
        service = DefaultStoppingService()
        ev1 = _make_evidence_projection("ev-log-1", SourceType.LOGS)
        context = _make_context([ev1])

        class PlanWithUnavailableSources:
            queries = []
            stop_reason = StopReason.SOURCES_UNAVAILABLE

        decision = service.evaluate(
            context=context,
            query_plan=PlanWithUnavailableSources(),
            round_number=1,
        )

        assert decision.action == StopAction.INCONCLUSIVE
        assert decision.stop_reason == StopReason.SOURCES_UNAVAILABLE
        assert decision.should_stop is True

    def test_continue_when_criteria_unmet_and_budget_remains(self) -> None:
        service = DefaultStoppingService(
            min_supporting_sources_for_adequate=2,
        )
        ev1 = _make_evidence_projection("ev-log-1", SourceType.LOGS)
        context = _make_context([ev1])

        h1 = Hypothesis(
            hypothesis_id="hyp-1",
            incident_id="inc-test-1",
            statement="Application bug causing crash",
            root_cause_category=RootCauseCategory.CODE_DEFECT,
            affected_component="order-service",
            supporting_evidence=[
                EvidenceCitation(
                    evidence_id="ev-log-1",
                    reason="Crash log",
                    role=EvidenceRole.EFFECT,
                )
            ],
        )
        hypotheses = HypothesisSet(
            incident_id="inc-test-1",
            revision=1,
            generated_at=NOW,
            hypotheses=[h1],
        )

        decision = service.evaluate(
            context=context,
            hypotheses=hypotheses,
            budget=InvestigationBudget(max_rounds=4),
            round_number=1,
        )

        assert decision.action == StopAction.CONTINUE
        assert decision.stop_reason is None
        assert decision.should_stop is False
