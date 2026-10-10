"""Canonical contracts for recommendation-only remediation.

These schemas define the structured output of the remediation planning node.
All remediation plans are strictly descriptive guidance for human operators;
they never execute shell commands or automated write operations.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from contracts.common import ContractModel
from contracts.enums import ConfidenceLabel, RootCauseCategory


class RemediationRisk(StrEnum):
    """Operational risk tier for proposed remediation actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class RemediationStep(ContractModel):
    """A single human-reviewable operational remediation step."""

    step_number: int = Field(ge=1, description="1-indexed execution order")
    title: str = Field(min_length=3, description="Short operator action summary")
    purpose: str = Field(min_length=5, description="Rationale for this step")
    instructions: list[str] = Field(min_length=1, description="Human-actionable guidance lines")
    expected_result: str = Field(min_length=3, description="Expected operational observation")
    verification: list[str] = Field(default_factory=list, description="Verification checks")
    rollback_guidance: list[str] = Field(default_factory=list, description="Safe reversal steps")
    requires_human_approval: bool = Field(default=True, description="Always true for safety")

    @model_validator(mode="after")
    def validate_human_approval(self) -> "RemediationStep":
        if not self.requires_human_approval:
            raise ValueError("remediation steps must require human approval (requires_human_approval must be True)")
        return self


class RemediationPlan(ContractModel):
    """Complete, evidence-linked remediation guidance plan."""

    plan_id: str = Field(..., description="Unique plan identifier")
    incident_id: str = Field(..., description="Target incident identifier")
    created_at: datetime = Field(..., description="UTC creation timestamp")
    recommendation_available: bool = Field(..., description="True if safe actions proposed")
    safety_notice: str = Field(..., description="Prominent human-review safety banner")
    hypothesis_id: str | None = Field(default=None, description="Ranked hypothesis addressed")
    root_cause_category: RootCauseCategory | None = Field(default=None, description="Root cause category")
    confidence: ConfidenceLabel | None = Field(default=None, description="Hypothesis confidence level")
    evidence_ids: list[str] = Field(default_factory=list, description="Cited evidence IDs")
    risk: RemediationRisk = Field(..., description="Operational risk tier")
    prerequisites: list[str] = Field(default_factory=list, description="Required initial state")
    steps: list[RemediationStep] = Field(default_factory=list, description="Sequential steps")
    escalation_guidance: list[str] = Field(default_factory=list, description="Escalation path")
    unresolved_uncertainty: list[str] = Field(default_factory=list, description="Remaining unknowns")

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> "RemediationPlan":
        if self.recommendation_available:
            if not self.hypothesis_id:
                raise ValueError("available remediation plan requires a hypothesis_id")
            if not self.root_cause_category:
                raise ValueError("available remediation plan requires a root_cause_category")
            if not self.confidence:
                raise ValueError("available remediation plan requires a confidence label")
            if not self.evidence_ids:
                raise ValueError("available remediation plan must cite at least one evidence_id")
            if not self.steps:
                raise ValueError("available remediation plan must contain at least one step")
            for idx, step in enumerate(self.steps, start=1):
                if step.step_number != idx:
                    raise ValueError(f"step at index {idx - 1} has step_number {step.step_number}, expected {idx}")
        else:
            if self.risk != RemediationRisk.BLOCKED:
                raise ValueError("unavailable remediation plan must have risk=BLOCKED")
            if self.steps:
                raise ValueError("unavailable remediation plan must have no recommended steps")
            if not self.escalation_guidance and not self.unresolved_uncertainty:
                raise ValueError("unavailable remediation plan must provide escalation_guidance or unresolved_uncertainty")
        return self
