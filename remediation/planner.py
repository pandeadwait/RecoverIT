"""Remediation planning service implementations.

Coordinates LLM reasoning with deterministic safety policies to formulate
safe, human-actionable operational remediation plans.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.enums import InvestigationStatus
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import RankedHypothesisSet
from contracts.remediation.schemas import RemediationPlan
from reasoning.provider.interface import ReasoningProvider
from remediation.policies import (
    create_blocked_remediation_plan,
    create_inconclusive_remediation_plan,
    validate_and_sanitize_remediation_plan,
)

logger = logging.getLogger(__name__)


class LLMRemediationPlanningService:
    """Production remediation planning service powered by a ReasoningProvider.

    Interacts with the configured ReasoningProvider to generate candidate plans,
    then rigorously validates them against deterministic safety policies.
    """

    def __init__(self, reasoning_provider: ReasoningProvider) -> None:
        self.reasoning_provider = reasoning_provider

    async def plan(
        self,
        ranked: RankedHypothesisSet | None,
        context: IncidentContextSnapshot | None,
    ) -> RemediationPlan:
        """Formulate a safe remediation plan for the given incident investigation state."""
        inc_id = "unknown"
        if ranked and getattr(ranked, "incident_id", None):
            inc_id = ranked.incident_id
        elif context and getattr(context, "incident_id", None):
            inc_id = context.incident_id

        # 1. Inconclusive or empty ranking check
        if (
            ranked is None
            or not getattr(ranked, "hypotheses", None)
            or getattr(ranked, "status", None) != InvestigationStatus.COMPLETED
        ):
            logger.info("Investigation inconclusive or incomplete; emitting safe inconclusive plan.")
            return create_inconclusive_remediation_plan(context, incident_id=inc_id)

        # 2. Invoke provider for candidate plan
        try:
            raw_plan = await self.reasoning_provider.generate_remediation(ranked, context)
        except Exception as exc:
            logger.warning(
                "Reasoning provider failed to generate remediation plan (%r); falling back to blocked plan.",
                exc,
            )
            top_hyp_id = (
                ranked.hypotheses[0].hypothesis_id
                if ranked.hypotheses
                else None
            )
            return create_blocked_remediation_plan(
                incident_id=inc_id,
                hypothesis_id=top_hyp_id,
                reason=f"Automated remediation reasoning failed: {exc}",
                escalation=[
                    "Conduct manual diagnostic investigation with service on-call engineer.",
                    "Verify service metrics and logs before applying manual mitigations.",
                ],
                uncertainty=[
                    f"Reasoning provider invocation error: {exc}",
                ],
            )

        # 3. Enforce deterministic policy validation and sanitization
        sanitized_plan = validate_and_sanitize_remediation_plan(raw_plan, ranked, context)
        return sanitized_plan
