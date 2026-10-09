"""Produce an explicit inconclusive terminal result."""

from __future__ import annotations

from contracts.enums import InvestigationState, InvestigationStatus, StopReason
from contracts.hypothesis.schemas import HypothesisSet
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def finish_inconclusive(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    decision = state.get("stop_decision")
    plan = state.get("query_plan")
    reason = (
        decision.reason
        if decision is not None
        else "No valid evidence queries could be planned."
    )
    stop_reason = (
        decision.stop_reason
        if decision is not None
        else (plan.stop_reason if plan is not None else StopReason.INSUFFICIENT_EVIDENCE)
    )
    hypotheses = state.get("hypotheses") or HypothesisSet(
        incident_id=state["incident"].incident_id,
        hypotheses=[],
        generated_at=dependencies.clock.now(),
    )
    result = dependencies.ranking_service.rank(
        hypotheses=hypotheses,
        context=state["context"],
        budget_usage=state["budget_usage"],
        status=InvestigationStatus.INCONCLUSIVE,
        stop_reason=stop_reason,
        remaining_uncertainty=(
            decision.unresolved_criteria if decision is not None else [reason]
        ),
    )
    return {
        "workflow_state": InvestigationState.INCONCLUSIVE,
        "ranked_result": result,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="status",
            stage="finish_inconclusive",
            title="Investigation ended inconclusively",
            detail=reason,
        ),
    }
