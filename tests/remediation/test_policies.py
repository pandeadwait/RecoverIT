"""Tests for remediation safety policies and validation."""

from datetime import datetime, timezone
import pytest

from contracts.enums import (
    ConfidenceLabel,
    EvidenceRole,
    InvestigationStatus,
    RootCauseCategory,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.enums import EvidenceType, Reliability, Severity, SourceType
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
from remediation.policies import (
    create_blocked_remediation_plan,
    create_inconclusive_remediation_plan,
    validate_and_sanitize_remediation_plan,
)


def _make_context() -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        snapshot_id="snap-1",
        incident_id="inc-1",
        revision=1,
        created_at=datetime.now(timezone.utc),
        incident=IncidentSummary(
            service="order-api",
            environment="prod",
            severity=Severity.CRITICAL,
            detected_at=datetime.now(timezone.utc),
            summary="High HTTP 500 error rate",
        ),
        evidence=[
            EvidenceSummaryProjection(
                evidence_id="ev_changes_101",
                source_type=SourceType.CHANGES,
                evidence_type=EvidenceType.CODE_CHANGE,
                summary="Reduced connection pool max size to 2",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            ),
            EvidenceSummaryProjection(
                evidence_id="ev_logs_202",
                source_type=SourceType.LOGS,
                evidence_type=EvidenceType.ERROR_EVENT,
                summary="Database connection pool timeout",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            ),
        ],
        timeline=[],
        relationships=[],
    )


def _make_ranked(
    confidence: ConfidenceLabel = ConfidenceLabel.HIGH,
    contradicting: bool = False,
    status: InvestigationStatus = InvestigationStatus.COMPLETED,
) -> RankedHypothesisSet:
    supporting = [
        EvidenceCitation(
            evidence_id="ev_changes_101",
            reason="Commit changed pool configuration",
            role=EvidenceRole.CAUSE,
        ),
        EvidenceCitation(
            evidence_id="ev_logs_202",
            reason="Logs show pool exhausted",
            role=EvidenceRole.EFFECT,
        ),
    ]
    contradictions = (
        [
            EvidenceCitation(
                evidence_id="ev_logs_202",
                reason="Conflicting timestamps",
                role=EvidenceRole.CONTRADICTION,
            )
        ]
        if contradicting
        else []
    )

    hyp = RankedHypothesis(
        rank=1,
        hypothesis_id="hyp-db-pool",
        statement="Database connection pool size was reduced below minimum required.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-api",
        evidence_score=92.5,
        confidence_label=confidence,
        supporting_evidence=supporting,
        contradicting_evidence=contradictions,
        score_breakdown=ScoreBreakdown(),
    )

    return RankedHypothesisSet(
        incident_id="inc-1",
        context_snapshot_id="snap-1",
        ranking_id="rank-1",
        created_at=datetime.now(timezone.utc),
        status=status,
        hypotheses=[hyp] if status == InvestigationStatus.COMPLETED else [],
        budget_usage=BudgetUsage(),
    )


def _make_valid_plan() -> RemediationPlan:
    step = RemediationStep(
        step_number=1,
        title="Restore database connection pool configuration",
        purpose="Ensure application configuration provides adequate connections",
        instructions=[
            "Inspect deployment configuration repository",
            "Restore max_pool_size parameter to verified value of 25",
        ],
        expected_result="Application configuration specifies max_pool_size=25",
        verification=["Verify database health endpoint reports UP status"],
        rollback_guidance=["Revert parameter change if memory thresholds are exceeded"],
        requires_human_approval=True,
    )
    return RemediationPlan(
        plan_id="plan-1",
        incident_id="inc-1",
        created_at=datetime.now(timezone.utc),
        recommendation_available=True,
        safety_notice="All remediation actions require operator approval.",
        hypothesis_id="hyp-db-pool",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        confidence=ConfidenceLabel.HIGH,
        evidence_ids=["ev_changes_101", "ev_logs_202"],
        risk=RemediationRisk.LOW,
        prerequisites=["Operator access to config repository"],
        steps=[step],
        escalation_guidance=[],
        unresolved_uncertainty=[],
    )


def test_valid_plan_passes_policy_unchanged():
    plan = _make_valid_plan()
    ranked = _make_ranked(confidence=ConfidenceLabel.HIGH)
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan, ranked, context)
    assert validated.recommendation_available is True
    assert validated.risk == RemediationRisk.LOW
    assert len(validated.steps) == 1
    assert validated.hypothesis_id == "hyp-db-pool"


def test_command_syntax_triggers_blocked_downgrade():
    plan = _make_valid_plan()
    # Inject shell command syntax into step instruction
    bad_step = RemediationStep(
        step_number=1,
        title="Restart the pod immediately",
        purpose="Quick recovery",
        instructions=["Execute: kubectl rollout undo deployment/order-api"],
        expected_result="Pod restarted",
        requires_human_approval=True,
    )
    plan_with_cmd = plan.model_copy(update={"steps": [bad_step]})
    ranked = _make_ranked()
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan_with_cmd, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert len(validated.steps) == 0
    assert "command syntax" in validated.safety_notice.lower()


def test_ungrounded_evidence_triggers_blocked_downgrade():
    plan = _make_valid_plan()
    # Cite an evidence ID that does not exist in context or hypothesis
    plan_ungrounded = plan.model_copy(update={"evidence_ids": ["ev_changes_101", "ev_hallucinated_999"]})
    ranked = _make_ranked()
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan_ungrounded, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert len(validated.steps) == 0
    assert "ungrounded evidence" in validated.safety_notice.lower()


def test_low_or_medium_confidence_downgrades_to_blocked():
    plan = _make_valid_plan()
    ranked = _make_ranked(confidence=ConfidenceLabel.MEDIUM)
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert "confidence is medium" in validated.safety_notice.lower()


def test_contradicting_evidence_downgrades_to_blocked():
    plan = _make_valid_plan()
    ranked = _make_ranked(confidence=ConfidenceLabel.HIGH, contradicting=True)
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert "contradicting evidence" in validated.safety_notice.lower()


def test_inconclusive_ranking_produces_inconclusive_blocked_plan():
    plan = _make_valid_plan()
    ranked = _make_ranked(status=InvestigationStatus.INCONCLUSIVE)
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert "inconclusive" in validated.safety_notice.lower()


def test_secret_pattern_triggers_blocked_downgrade():
    plan = _make_valid_plan()
    bad_step = RemediationStep(
        step_number=1,
        title="Apply credentials",
        purpose="Update settings",
        instructions=["Set password: supersecretpassword123 in config file"],
        expected_result="Config updated",
        requires_human_approval=True,
    )
    plan_with_secret = plan.model_copy(update={"steps": [bad_step]})
    ranked = _make_ranked()
    context = _make_context()

    validated = validate_and_sanitize_remediation_plan(plan_with_secret, ranked, context)
    assert validated.recommendation_available is False
    assert validated.risk == RemediationRisk.BLOCKED
    assert "secret or credential" in validated.safety_notice.lower()
