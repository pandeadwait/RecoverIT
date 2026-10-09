"""Calculate the final deterministic hypothesis ranking."""

from __future__ import annotations

from contracts.enums import InvestigationState, InvestigationStatus, StopReason
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def rank_hypotheses(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    hypotheses = state.get("hypotheses")
    if hypotheses is None:
        raise ValueError("ranking requires hypotheses")
    decision = state.get("stop_decision")
    result = dependencies.ranking_service.rank(
        hypotheses=hypotheses,
        context=state["context"],
        budget_usage=state["budget_usage"],
        status=InvestigationStatus.COMPLETED,
        stop_reason=(decision.stop_reason if decision else StopReason.SUFFICIENT_EVIDENCE),
        remaining_uncertainty=(decision.unresolved_criteria if decision else []),
    )
    return {
        "workflow_state": InvestigationState.COMPLETED,
        "ranked_result": result,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="status",
            stage="rank_hypotheses",
            title="Investigation completed",
        ),
    }
