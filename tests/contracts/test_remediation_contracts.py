"""Tests for canonical remediation contracts (Person 1)."""

from datetime import datetime, timezone
import json
import pytest
from pydantic import ValidationError

from contracts.enums import ConfidenceLabel, RootCauseCategory
from contracts.remediation.schemas import (
    RemediationPlan,
    RemediationRisk,
    RemediationStep,
)


def _make_sample_step(step_number: int = 1) -> RemediationStep:
    return RemediationStep(
        step_number=step_number,
        title="Verify database connection pool setting",
        purpose="Ensure the configuration revision reflects intended values",
        instructions=[
            "Inspect application config file in repository",
            "Verify max_connections is restored to 50",
        ],
        expected_result="Connection pool configuration shows 50 connections",
        verification=["Run read-only health probe against database check endpoint"],
        rollback_guidance=["Re-apply previous revision if connection errors persist"],
        requires_human_approval=True,
    )


def _make_sample_available_plan() -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-test-001",
        incident_id="inc-test-001",
        created_at=datetime.now(timezone.utc),
        recommendation_available=True,
        safety_notice="All actions require human operator review and change approval.",
        hypothesis_id="hyp-001",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        confidence=ConfidenceLabel.HIGH,
        evidence_ids=["ev_changes_commit_123", "ev_logs_456"],
        risk=RemediationRisk.LOW,
        prerequisites=["Access to repository and deployment approval"],
        steps=[_make_sample_step(1), _make_sample_step(2)],
        escalation_guidance=[],
        unresolved_uncertainty=[],
    )


def test_remediation_step_valid_and_serialized():
    step = _make_sample_step(1)
    data = json.loads(step.model_dump_json())
    assert data["step_number"] == 1
    assert data["requires_human_approval"] is True
    assert data["schema_version"] == "1.0"

    # Reconstruct from JSON
    rebuilt = RemediationStep.model_validate(data)
    assert rebuilt == step


def test_remediation_step_rejects_extra_fields():
    step_dict = _make_sample_step(1).model_dump()
    step_dict["unknown_field"] = "malicious_injection"
    with pytest.raises(ValidationError):
        RemediationStep.model_validate(step_dict)


def test_remediation_step_enforces_human_approval():
    step_dict = _make_sample_step(1).model_dump()
    step_dict["requires_human_approval"] = False
    with pytest.raises(ValidationError, match="requires_human_approval must be True"):
        RemediationStep.model_validate(step_dict)


def test_remediation_plan_available_roundtrip():
    plan = _make_sample_available_plan()
    dumped = json.loads(plan.model_dump_json())
    assert dumped["schema_version"] == "1.0"
    assert dumped["recommendation_available"] is True
    assert dumped["risk"] == "low"
    assert len(dumped["steps"]) == 2

    rebuilt = RemediationPlan.model_validate(dumped)
    assert rebuilt == plan


def test_remediation_plan_available_requires_mandatory_fields():
    # Missing hypothesis_id
    with pytest.raises(ValidationError, match="requires a hypothesis_id"):
        RemediationPlan(
            plan_id="p-1",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=True,
            safety_notice="Notice",
            hypothesis_id=None,
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            confidence=ConfidenceLabel.HIGH,
            evidence_ids=["ev-1"],
            risk=RemediationRisk.LOW,
            steps=[_make_sample_step(1)],
        )

    # Missing evidence_ids
    with pytest.raises(ValidationError, match="cite at least one evidence_id"):
        RemediationPlan(
            plan_id="p-1",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=True,
            safety_notice="Notice",
            hypothesis_id="hyp-1",
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            confidence=ConfidenceLabel.HIGH,
            evidence_ids=[],
            risk=RemediationRisk.LOW,
            steps=[_make_sample_step(1)],
        )

    # Missing steps
    with pytest.raises(ValidationError, match="contain at least one step"):
        RemediationPlan(
            plan_id="p-1",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=True,
            safety_notice="Notice",
            hypothesis_id="hyp-1",
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            confidence=ConfidenceLabel.HIGH,
            evidence_ids=["ev-1"],
            risk=RemediationRisk.LOW,
            steps=[],
        )


def test_remediation_plan_enforces_consecutive_step_ordering():
    with pytest.raises(ValidationError, match="expected 2"):
        RemediationPlan(
            plan_id="p-1",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=True,
            safety_notice="Notice",
            hypothesis_id="hyp-1",
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            confidence=ConfidenceLabel.HIGH,
            evidence_ids=["ev-1"],
            risk=RemediationRisk.LOW,
            steps=[_make_sample_step(1), _make_sample_step(3)],  # 1 then 3 instead of 2
        )


def test_remediation_plan_unavailable_validation():
    # Valid unavailable plan
    blocked_plan = RemediationPlan(
        plan_id="p-blocked",
        incident_id="inc-1",
        created_at=datetime.now(timezone.utc),
        recommendation_available=False,
        safety_notice="No production change recommended. Operator escalation required.",
        risk=RemediationRisk.BLOCKED,
        escalation_guidance=["Escalate to database on-call team for direct investigation"],
        unresolved_uncertainty=["Root cause could not be determined with sufficient confidence"],
    )
    assert blocked_plan.recommendation_available is False
    assert blocked_plan.risk == RemediationRisk.BLOCKED

    # Invalid: unavailable plan with non-BLOCKED risk
    with pytest.raises(ValidationError, match="risk=BLOCKED"):
        RemediationPlan(
            plan_id="p-blocked",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=False,
            safety_notice="Notice",
            risk=RemediationRisk.LOW,
            escalation_guidance=["Escalate"],
        )

    # Invalid: unavailable plan with steps
    with pytest.raises(ValidationError, match="no recommended steps"):
        RemediationPlan(
            plan_id="p-blocked",
            incident_id="inc-1",
            created_at=datetime.now(timezone.utc),
            recommendation_available=False,
            safety_notice="Notice",
            risk=RemediationRisk.BLOCKED,
            steps=[_make_sample_step(1)],
            escalation_guidance=["Escalate"],
        )
