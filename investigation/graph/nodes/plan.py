"""Plan source-neutral evidence queries."""

from __future__ import annotations

from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def plan_queries(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    assessment = state.get("missing_information")
    if assessment is None:
        raise ValueError("assess_gaps must set missing_information before planning")
    plan = await dependencies.query_planning_service.plan(
        assessment=assessment,
        capabilities=state["source_capabilities"],
        context=state["context"],
        hypotheses=state.get("hypotheses"),
        budget=state["budget"],
        budget_usage=state["budget_usage"],
        round_number=state["round_number"],
        query_history=list(state.get("query_history", [])),
    )
    return {
        "query_plan": plan,
        "query_history": [*state.get("query_history", []), plan],
        "progress_events": progress_update(
            state,
            dependencies,
            kind="reasoning",
            stage="plan_queries",
            title="Evidence queries planned",
            metadata={"query_count": len(plan.queries)},
        ),
    }
