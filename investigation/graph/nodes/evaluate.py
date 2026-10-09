"""Apply deterministic investigation stopping rules."""

from __future__ import annotations

from contracts.enums import StopAction
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def evaluate_stopping(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    assessment = state.get("missing_information")
    plan = state.get("query_plan")
    if assessment is None or plan is None:
        raise ValueError("stopping evaluation requires assessment and query plan")
    decision = dependencies.stopping_service.evaluate(
        context=state["context"],
        hypotheses=state.get("hypotheses"),
        assessment=assessment,
        query_plan=plan,
        budget=state["budget"],
        budget_usage=state["budget_usage"],
        round_number=state["round_number"],
    )
    update: dict[str, object] = {
        "stop_decision": decision,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="decision",
            stage="evaluate_stopping",
            title="Stopping rules evaluated",
            detail=decision.reason,
            metadata={"action": str(decision.action)},
        ),
    }
    if decision.action == StopAction.CONTINUE:
        update["round_number"] = state["round_number"] + 1
    return update
