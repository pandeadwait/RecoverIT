"""Tests for LLMRemediationPlanningService and ScriptedRemediationPlanningService."""

from datetime import datetime, timezone
import pytest

from contracts.enums import (
    ConfidenceLabel,
    EvidenceRole,
    EvidenceType,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceType,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    RankedHypothesis,
    RankedHypothesisSet,
    ScoreBreakdown,
)
from contracts.investigation.schemas import BudgetUsage
from contracts.remediation.schemas import (
    RemediationPlan,
    RemediationRisk,
    RemediationStep,
)
from remediation.planner import LLMRemediationPlanningService
from tests.support.scripted_reasoning_provider import ScriptedReasoningProvider
from tests.support.scripted_remediation_planner import ScriptedRemediationPlanningService


def _make_context() -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        snapshot_id="snap-100",
        incident_id="inc-100",
        revision=1,
        created_at=datetime.now(timezone.utc),
        incident=IncidentSummary(
            service="checkout-service",
            environment="prod",
            severity=Severity.CRITICAL,
            detected_at=datetime.now(timezone.utc),
            summary="Payment gateway timeout spike",
        ),
        evidence=[
            EvidenceSummaryProjection(
                evidence_id="ev_cfg_01",
                source_type=SourceType.CONFIGURATION,
                evidence_type=EvidenceType.CONFIGURATION_CHANGE,
                summary="Reduced connection timeout from 30s to 1s",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            ),
        ],
        timeline=[],
        relationships=[],
    )


def _make_ranked(
    confidence: ConfidenceLabel = ConfidenceLabel.HIGH,
    status: InvestigationStatus = InvestigationStatus.COMPLETED,
) -> RankedHypothesisSet:
    return RankedHypothesisSet(
        incident_id="inc-100",
        context_snapshot_id="snap-100",
        ranking_id="rank-100",
        created_at=datetime.now(timezone.utc),
        status=status,
        hypotheses=[
            RankedHypothesis(
                hypothesis_id="hyp-100",
                rank=1,
                statement="Connection timeout value was reduced causing widespread timeouts under load",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="checkout-service",
                evidence_score=92.0,
                confidence_label=confidence,
                supporting_evidence=[
                    EvidenceCitation(
                        evidence_id="ev_cfg_01",
                        reason="Direct config change commit",
                        role=EvidenceRole.CAUSE,
                    )
                ],
                contradicting_evidence=[],
                unresolved_questions=[],
                score_breakdown=ScoreBreakdown(),
            )
        ],
        budget_usage=BudgetUsage(
            rounds=1,
            queries=2,
            reasoning_calls=1,
            input_units=1000,
            output_units=200,
        ),
    )


def _make_valid_plan() -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-valid-100",
        incident_id="inc-100",
        created_at=datetime.now(timezone.utc),
        recommendation_available=True,
        safety_notice="HUMAN OPERATOR APPROVAL MANDATORY before applying changes.",
        hypothesis_id="hyp-100",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        confidence=ConfidenceLabel.HIGH,
        evidence_ids=["ev_cfg_01"],
        risk=RemediationRisk.LOW,
        prerequisites=["Ensure change window is open"],
        steps=[
            RemediationStep(
                step_number=1,
                title="Revert timeout setting to 30s in checkout-service config",
                purpose="Restore original operational timeout setting",
                instructions=[
                    "Open the service configuration repository",
                    "Update connection_timeout_seconds from 1 to 30",
                    "Submit PR and request team review",
                ],
                expected_result="Gateway timeouts resolve",
                verification=["Observe 5xx error rate returns below 0.1%"],
                rollback_guidance=["Revert PR if connection spikes exceed limit"],
                requires_human_approval=True,
            )
        ],
        escalation_guidance=[],
        unresolved_uncertainty=[],
    )


@pytest.mark.asyncio
async def test_llm_planner_successful_flow():
    valid_plan = _make_valid_plan()
    provider = ScriptedReasoningProvider(custom_remediation=valid_plan)
    service = LLMRemediationPlanningService(reasoning_provider=provider)

    ranked = _make_ranked()
    context = _make_context()

    plan = await service.plan(ranked, context)

    assert plan.recommendation_available is True
    assert plan.risk == RemediationRisk.LOW
    assert len(plan.steps) == 1
    assert plan.steps[0].requires_human_approval is True
    assert plan.evidence_ids == ["ev_cfg_01"]


@pytest.mark.asyncio
async def test_llm_planner_inconclusive_ranking_bypasses_provider():
    valid_plan = _make_valid_plan()
    provider = ScriptedReasoningProvider(custom_remediation=valid_plan)
    service = LLMRemediationPlanningService(reasoning_provider=provider)

    ranked = _make_ranked(status=InvestigationStatus.INCONCLUSIVE)
    context = _make_context()

    plan = await service.plan(ranked, context)

    assert plan.recommendation_available is False
    assert plan.risk == RemediationRisk.BLOCKED
    assert len(plan.steps) == 0
    assert len(plan.escalation_guidance) > 0
    # Provider should not even be called when ranking is inconclusive
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_llm_planner_provider_exception_falls_back_to_blocked():
    class FailingProvider:
        async def generate_remediation(self, ranked, context):
            raise RuntimeError("Live LLM API connection timed out")

    service = LLMRemediationPlanningService(reasoning_provider=FailingProvider())
    ranked = _make_ranked()
    context = _make_context()

    plan = await service.plan(ranked, context)

    assert plan.recommendation_available is False
    assert plan.risk == RemediationRisk.BLOCKED
    assert len(plan.steps) == 0
    assert "Live LLM API connection timed out" in plan.unresolved_uncertainty[0]


@pytest.mark.asyncio
async def test_llm_planner_sanitizes_dangerous_provider_output():
    # Plan contains command syntax
    dangerous_plan = _make_valid_plan().model_copy(
        update={
            "steps": [
                RemediationStep(
                    step_number=1,
                    title="Rollout restart pod",
                    purpose="Restart the container",
                    instructions=["Run kubectl rollout restart deployment/checkout-service"],
                    expected_result="Pod restarts",
                    requires_human_approval=True,
                )
            ]
        }
    )
    provider = ScriptedReasoningProvider(custom_remediation=dangerous_plan)
    service = LLMRemediationPlanningService(reasoning_provider=provider)

    ranked = _make_ranked()
    context = _make_context()

    plan = await service.plan(ranked, context)

    assert plan.recommendation_available is False
    assert plan.risk == RemediationRisk.BLOCKED
    assert len(plan.steps) == 0
    assert "syntax blocked" in plan.unresolved_uncertainty[0].lower()


@pytest.mark.asyncio
async def test_llm_planner_sanitizes_ungrounded_evidence():
    ungrounded_plan = _make_valid_plan().model_copy(
        update={"evidence_ids": ["ev_cfg_01", "ev_hallucinated_404"]}
    )
    provider = ScriptedReasoningProvider(custom_remediation=ungrounded_plan)
    service = LLMRemediationPlanningService(reasoning_provider=provider)

    ranked = _make_ranked()
    context = _make_context()

    plan = await service.plan(ranked, context)

    assert plan.recommendation_available is False
    assert plan.risk == RemediationRisk.BLOCKED
    assert "ev_hallucinated_404" in plan.safety_notice


@pytest.mark.asyncio
async def test_scripted_remediation_planning_service():
    scripted_svc = ScriptedRemediationPlanningService()

    ranked = _make_ranked()
    context = _make_context()

    plan = await scripted_svc.plan(ranked, context)

    assert plan.recommendation_available is True
    assert plan.risk == RemediationRisk.LOW
    assert len(plan.steps) == 1
    assert plan.steps[0].requires_human_approval is True
    assert scripted_svc.call_count == 1

    # Inconclusive ranking
    scripted_svc.reset_calls()
    inconclusive_ranked = _make_ranked(status=InvestigationStatus.INCONCLUSIVE)
    plan_inconc = await scripted_svc.plan(inconclusive_ranked, context)
    assert plan_inconc.recommendation_available is False
    assert plan_inconc.risk == RemediationRisk.BLOCKED


@pytest.mark.asyncio
async def test_scripted_remediation_planning_service_failure_simulation():
    failing_svc = ScriptedRemediationPlanningService(should_fail=True)
    with pytest.raises(RuntimeError, match="Simulated planner failure"):
        await failing_svc.plan(None, None)
