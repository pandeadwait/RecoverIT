"""Remediation planning graph node for operator guidance."""

from __future__ import annotations

from typing import Any

from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def plan_remediation(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    """Generate human-actionable operator remediation recommendations."""

    ranked = state.get("ranked_result")
    context = state.get("context")

    if dependencies.remediation_planning_service is None:
        return {
            "remediation_plan": None,
            "progress_events": progress_update(
                state,
                dependencies,
                kind="status",
                stage="plan_remediation",
                title="Remediation planning skipped (no service configured)",
            ),
        }

    plan = await dependencies.remediation_planning_service.plan(
        ranked=ranked,
        context=context,
    )

    safety_notice = getattr(plan, "safety_notice", "")
    if isinstance(plan, dict):
        safety_notice = plan.get("safety_notice", "")

    return {
        "remediation_plan": plan,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="status",
            stage="plan_remediation",
            title="Remediation guidance prepared",
            detail=safety_notice,
        ),
    }


__all__ = ["plan_remediation"]
