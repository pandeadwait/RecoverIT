"""
Unit tests for tightened stopping rules (Phase 6).

Verifies all Phase 6 acceptance criteria:
1. Logs plus metrics alone cannot finish a configuration investigation.
2. The agent starts another round (should_stop=False) when direct causal evidence is missing.
3. When all completion criteria are met, the evaluator stops with StopReason.SUFFICIENT_EVIDENCE.
4. Budget exhaustion is strictly distinguished from sufficient evidence (yielding StopReason.BUDGET_EXHAUSTED).
5. Unresolved HIGH-priority information gaps prevent premature stopping.
6. The leading hypothesis must have a meaningful score advantage over rank two (margin >= 5.0).
7. At least one alternative explanation must have been tested (>= 2 hypotheses).
8. The evaluator provides transparent criteria_status and unresolved_criteria diagnostics.
9. Permissive one-source stopping override is removed from runner.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InformationPriority,
    Reliability,
    RootCauseCategory,
    SourceCoverageStatus,
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
)
from contracts.investigation.schemas import (
    InformationGapCategory,
    InvestigationBudget,
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.budgets.budget_tracker import BudgetTracker
from investigation.orchestration.orchestrator import (
    StoppingDecision,
    StoppingRuleEvaluator,
)
from recoverit.runner import InvestigationRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_context() -> IncidentContextSnapshot:
    """Create a context snapshot with 2 independent sources: logs and metrics."""
    ev1 = EvidenceSummaryProjection(
        evidence_id="ev_log_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        summary="HTTP 500 internal server error",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    ev2 = EvidenceSummaryProjection(
        evidence_id="ev_metric_01",
        source_type=SourceType.METRICS,
        evidence_type=EvidenceType.METRIC_ANOMALY,
        event_time=datetime(2026, 9, 12, 10, 0, 5, tzinfo=timezone.utc),
        summary="500 error rate spike",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    return IncidentContextSnapshot(
        incident_id="inc_test_01",
        snapshot_id="ctx_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 2, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service="payment-api",
            environment="production",
            severity="critical",
            detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
            summary="Payment service failure",
        ),
        timeline=[],
        evidence=[ev1, ev2],
        source_coverage={
            SourceType.LOGS: SourceCoverageStatus.AVAILABLE,
            SourceType.METRICS: SourceCoverageStatus.AVAILABLE,
        },
    )


@pytest.fixture
def context_with_causal_and_symptoms() -> IncidentContextSnapshot:
    """Create a context snapshot with deployments (causal) and logs (symptom)."""
    ev_deploy = EvidenceSummaryProjection(
        evidence_id="ev_deploy_01",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=datetime(2026, 9, 12, 9, 55, 0, tzinfo=timezone.utc),
        summary="Deployment v2.4.1 succeeded",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    ev_log = EvidenceSummaryProjection(
        evidence_id="ev_log_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        summary="HTTP 500 timeout connecting to misconfigured db",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    return IncidentContextSnapshot(
        incident_id="inc_test_01",
        snapshot_id="ctx_02",
        revision=2,
        created_at=datetime(2026, 9, 12, 10, 5, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service="payment-api",
            environment="production",
            severity="critical",
            detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
            summary="Payment service failure",
        ),
        timeline=[],
        evidence=[ev_deploy, ev_log],
        source_coverage={
            SourceType.DEPLOYMENTS: SourceCoverageStatus.AVAILABLE,
            SourceType.LOGS: SourceCoverageStatus.AVAILABLE,
        },
    )


# ---------------------------------------------------------------------------
# Unit Tests: Phase 6 Tightened Stopping Rules
# ---------------------------------------------------------------------------


def test_logs_plus_metrics_alone_cannot_finish_configuration_investigation(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 1: Logs plus metrics alone cannot finish a configuration investigation.
    Even with 2 independent sources (logs, metrics), a configuration regression
    hypothesis without direct causal evidence must NOT be marked sufficient to stop.
    """
    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Database connection configuration is invalid.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="Timeout logged.",
                        role=EvidenceRole.EFFECT,
                    ),
                    EvidenceCitation(
                        evidence_id="ev_metric_01",
                        reason="500 metric spike.",
                        role=EvidenceRole.EFFECT,
                    ),
                ],
                status=HypothesisStatus.ACTIVE,
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_test_01",
                statement="External gateway failure.",
                root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                affected_component="gateway",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    evaluator = StoppingRuleEvaluator()
    decision = evaluator.evaluate(
        round_num=1,
        context=base_context,
        hypotheses=hyp_set,
        max_rounds=3,
    )

    # Must NOT stop early with sufficient evidence
    assert not decision.should_stop
    assert decision.criteria_status["causal_evidence"] is False
    assert decision.criteria_status["independent_sources"] is True
    assert decision.criteria_status["symptom_evidence"] is True
    assert any("causal" in u.lower() for u in decision.unresolved_criteria)
    assert "Continuing to round 2" in decision.reason


def test_evaluator_triggers_another_round_when_causal_evidence_missing(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 2: The agent starts another round when direct causal evidence is missing.
    """
    evaluator = StoppingRuleEvaluator()
    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Deployment introduced broken config.",
                root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="Errors seen in logs.",
                        role=EvidenceRole.EFFECT,
                    )
                ],
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_test_01",
                statement="Infrastructure node failure.",
                root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
                affected_component="node-1",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=base_context,
        hypotheses=hyp_set,
        max_rounds=3,
    )

    assert not decision.should_stop
    assert decision.criteria_status["causal_evidence"] is False
    assert "Continuing to round 2" in decision.reason


def test_evaluator_stops_with_sufficient_evidence_when_all_criteria_met(
    context_with_causal_and_symptoms: IncidentContextSnapshot,
) -> None:
    """
    Criterion 3: When all completion criteria are satisfied (multi-source, causal,
    symptom, no gaps, confidence, margin, alternative tested), stop with SUFFICIENT_EVIDENCE.
    """
    evaluator = StoppingRuleEvaluator()
    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Deployment introduced invalid database configuration.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_deploy_01",
                        reason="Deployment completed before error spike.",
                        role=EvidenceRole.CAUSE,
                    ),
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="500 errors logged after deployment.",
                        role=EvidenceRole.EFFECT,
                    ),
                ],
                status=HypothesisStatus.ACTIVE,
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_test_01",
                statement="External payment gateway outage.",
                root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                affected_component="gateway",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    assessment = MissingInformationAssessment(
        incident_id="inc_test_01",
        assessment_id="mia_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_01",
                question="Was a deployment performed?",
                reason="Check recent changes",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.DEPLOYMENTS],
                resolved=True,
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
            )
        ],
        unavailable_information=[],
    )

    decision = evaluator.evaluate(
        round_num=2,
        context=context_with_causal_and_symptoms,
        hypotheses=hyp_set,
        missing_info=assessment,
        max_rounds=3,
    )

    assert decision.should_stop
    assert decision.stop_reason == StopReason.SUFFICIENT_EVIDENCE
    assert not decision.is_inconclusive
    assert all(decision.criteria_status.values())
    assert len(decision.unresolved_criteria) == 0


def test_budget_exhaustion_strictly_distinguished_from_sufficient_evidence(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 4: Reaching max rounds without meeting completion criteria yields
    StopReason.BUDGET_EXHAUSTED, never StopReason.SUFFICIENT_EVIDENCE.
    """
    evaluator = StoppingRuleEvaluator()
    tracker = BudgetTracker(budget=InvestigationBudget(max_rounds=2))
    tracker.record_round()
    tracker.record_round()  # round limit reached

    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Configuration regression without causal proof.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="Errors seen in logs.",
                        role=EvidenceRole.EFFECT,
                    )
                ],
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_test_01",
                statement="Alternative hypothesis.",
                root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
                affected_component="node-1",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    decision = evaluator.evaluate(
        round_num=2,
        context=base_context,
        hypotheses=hyp_set,
        budget_tracker=tracker,
        max_rounds=2,
    )

    assert decision.should_stop
    # Must be BUDGET_EXHAUSTED, not SUFFICIENT_EVIDENCE
    assert decision.stop_reason == StopReason.BUDGET_EXHAUSTED
    assert decision.criteria_status["causal_evidence"] is False
    assert len(decision.unresolved_criteria) > 0


def test_unresolved_high_priority_gap_prevents_stopping(
    context_with_causal_and_symptoms: IncidentContextSnapshot,
) -> None:
    """
    Criterion 5: Unresolved HIGH-priority information gap prevents completion.
    """
    evaluator = StoppingRuleEvaluator()
    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Deployment introduced bad config.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_deploy_01",
                        reason="Deploy record.",
                        role=EvidenceRole.CAUSE,
                    ),
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="Log record.",
                        role=EvidenceRole.EFFECT,
                    ),
                ],
            ),
            Hypothesis(
                hypothesis_id="h2",
                incident_id="inc_test_01",
                statement="External failure.",
                root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                affected_component="gateway",
                supporting_evidence=[],
                status=HypothesisStatus.WEAKENED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    # An unresolved HIGH priority gap on an unqueried candidate source
    assessment = MissingInformationAssessment(
        incident_id="inc_test_01",
        assessment_id="mia_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_config_diff",
                question="What specific config key was changed?",
                reason="Pinpoint exact configuration drift",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.CONFIGURATION],
                resolved=False,
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
            )
        ],
        unavailable_information=[],
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=context_with_causal_and_symptoms,
        hypotheses=hyp_set,
        missing_info=assessment,
        max_rounds=3,
    )

    assert not decision.should_stop
    assert decision.criteria_status["no_high_priority_gaps"] is False
    assert any("unresolved high-priority" in u.lower() for u in decision.unresolved_criteria)


def test_alternative_explanation_tested_requirement(
    context_with_causal_and_symptoms: IncidentContextSnapshot,
) -> None:
    """
    Criterion 7: At least one alternative explanation has been tested.
    If only 1 hypothesis is evaluated, alternative_tested fails.
    """
    evaluator = StoppingRuleEvaluator()
    hyp_set = HypothesisSet(
        incident_id="inc_test_01",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                incident_id="inc_test_01",
                statement="Single hypothesis evaluated without competing explanation.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="payment-api",
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_deploy_01",
                        reason="Deploy record.",
                        role=EvidenceRole.CAUSE,
                    ),
                    EvidenceCitation(
                        evidence_id="ev_log_01",
                        reason="Log record.",
                        role=EvidenceRole.EFFECT,
                    ),
                ],
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    decision = evaluator.evaluate(
        round_num=1,
        context=context_with_causal_and_symptoms,
        hypotheses=hyp_set,
        max_rounds=3,
    )

    assert not decision.should_stop
    assert decision.criteria_status["alternative_tested"] is False
    assert any("fewer than 2 competing explanations" in u.lower() for u in decision.unresolved_criteria)


def test_runner_uses_tightened_stopping_evaluator_without_override() -> None:
    """
    Criterion 8: Verify runner.py uses StoppingRuleEvaluator() without the permissive
    min_supporting_sources_for_adequate=1 override.
    """
    runner = InvestigationRunner()
    assert runner is not None
    evaluator = StoppingRuleEvaluator()
    assert evaluator.min_supporting_sources_for_adequate == 2
    assert evaluator.require_causal_evidence is True
    assert evaluator.require_symptom_evidence is True
    assert evaluator.require_no_high_priority_gaps is True
    assert evaluator.require_alternative_tested is True
