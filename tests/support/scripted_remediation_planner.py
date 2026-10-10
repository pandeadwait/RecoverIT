"""Deterministic test double for RemediationPlanningService.

Provides predictable remediation plans for integration and graph unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contracts.enums import ConfidenceLabel, InvestigationStatus
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import RankedHypothesisSet
from contracts.remediation.schemas import (
    RemediationPlan,
    RemediationRisk,
    RemediationStep,
)
from remediation.policies import (
    create_blocked_remediation_plan,
    create_inconclusive_remediation_plan,
)


class ScriptedRemediationPlanningService:
    """Test double providing scripted or heuristically synthesized remediation plans."""

    def __init__(
        self,
        custom_plan: RemediationPlan | None = None,
        should_fail: bool = False,
        failure_exception: Exception | None = None,
    ) -> None:
        self.custom_plan = custom_plan
        self.should_fail = should_fail
        self.failure_exception = failure_exception or RuntimeError("Simulated planner failure")
        self._calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self._calls)

    def reset_calls(self) -> None:
        self._calls.clear()

    async def plan(
        self,
        ranked: RankedHypothesisSet | None,
        context: IncidentContextSnapshot | None,
    ) -> RemediationPlan:
        self._calls.append({
            "method": "plan",
            "has_ranked": ranked is not None,
            "has_context": context is not None,
        })

        if self.should_fail:
            raise self.failure_exception

        if self.custom_plan is not None:
            return self.custom_plan

        inc_id = "unknown"
        if ranked and getattr(ranked, "incident_id", None):
            inc_id = ranked.incident_id
        elif context and getattr(context, "incident_id", None):
            inc_id = context.incident_id

        # Return inconclusive plan if ranking is missing or not completed
        if (
            ranked is None
            or not getattr(ranked, "hypotheses", None)
            or getattr(ranked, "status", None) != InvestigationStatus.COMPLETED
        ):
            return create_inconclusive_remediation_plan(context, incident_id=inc_id)

        top_hyp = ranked.hypotheses[0]
        now = datetime.now(timezone.utc)

        # Ground evidence citations
        evidence_ids: list[str] = []
        if getattr(top_hyp, "supporting_evidence", None):
            evidence_ids.extend(c.evidence_id for c in top_hyp.supporting_evidence)
        if not evidence_ids and context and getattr(context, "evidence", None):
            evidence_ids.append(context.evidence[0].evidence_id)
        if not evidence_ids:
            evidence_ids.append("ev_simulated_01")

        # Low or medium confidence downgrades to blocked plan
        conf_str = str(getattr(top_hyp.confidence_label, "value", top_hyp.confidence_label)).lower()
        if conf_str != ConfidenceLabel.HIGH.value.lower():
            return create_blocked_remediation_plan(
                incident_id=inc_id,
                hypothesis_id=top_hyp.hypothesis_id,
                reason=f"Top hypothesis confidence is {conf_str}; only HIGH confidence qualifies for remediation recommendations.",
                escalation=[f"Consult with owner of component '{top_hyp.affected_component}'."],
            )

        return RemediationPlan(
            plan_id=f"plan-scripted-{inc_id}",
            incident_id=inc_id,
            created_at=now,
            recommendation_available=True,
            safety_notice="HUMAN OPERATOR APPROVAL MANDATORY. Review proposed operational guidance before taking action.",
            hypothesis_id=top_hyp.hypothesis_id,
            root_cause_category=top_hyp.root_cause_category,
            confidence=top_hyp.confidence_label,
            evidence_ids=evidence_ids,
            risk=RemediationRisk.LOW,
            prerequisites=[
                f"Verify operational permissions for service '{top_hyp.affected_component}'.",
                "Ensure staging rollback validation was completed.",
            ],
            steps=[
                RemediationStep(
                    step_number=1,
                    title=f"Review and adjust configuration for {top_hyp.affected_component}",
                    purpose=f"Mitigate root cause category {str(getattr(top_hyp.root_cause_category, 'value', top_hyp.root_cause_category))}.",
                    instructions=[
                        f"Check active configuration values for {top_hyp.affected_component} in deployment repository.",
                        "Verify metric stabilization after applying corrective changes.",
                    ],
                    expected_result="Service health telemetry returns to normal operating parameters.",
                    verification=["Verify HTTP 5xx error rate returns below 0.1% for 10 minutes."],
                    rollback_guidance=["Revert modified configuration values to previous known-good release."],
                    requires_human_approval=True,
                )
            ],
            escalation_guidance=[
                f"Escalate to primary on-call engineer for {top_hyp.affected_component}.",
            ],
            unresolved_uncertainty=[],
        )
