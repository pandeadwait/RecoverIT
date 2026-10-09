"""Generate or revise evidence-linked hypotheses."""

from __future__ import annotations

from contracts.enums import InvestigationState
from contracts.investigation.schemas import BudgetUsage
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def hypothesize(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    current = state.get("hypotheses")
    if current is None:
        hypotheses = await dependencies.hypothesis_service.generate(
            incident=state["incident"],
            context=state["context"],
            budget=state["budget"],
        )
    else:
        assessment = state.get("missing_information")
        if assessment is None:
            raise ValueError("hypothesis revision requires an assessment")
        hypotheses = await dependencies.hypothesis_service.revise(
            incident=state["incident"],
            previous_hypotheses=current,
            context=state["context"],
            assessment=assessment,
        )
    usage = state["budget_usage"]
    return {
        "workflow_state": InvestigationState.GENERATING_HYPOTHESES,
        "hypotheses": hypotheses,
        "budget_usage": BudgetUsage(
            rounds=usage.rounds,
            queries=usage.queries,
            reasoning_calls=usage.reasoning_calls + 1,
            input_units=usage.input_units,
            output_units=usage.output_units,
        ),
        "progress_events": progress_update(
            state,
            dependencies,
            kind="reasoning",
            stage="hypothesize",
            title="Hypotheses updated",
            metadata={"hypothesis_count": len(hypotheses.hypotheses)},
        ),
    }
