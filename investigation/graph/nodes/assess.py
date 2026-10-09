"""Assess known facts and unresolved information gaps."""

from __future__ import annotations

from contracts.enums import InvestigationState
from contracts.investigation.schemas import BudgetUsage
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def assess_gaps(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    assessment = await dependencies.missing_information_service.assess(
        incident=state["incident"],
        capabilities=state["source_capabilities"],
        context=state["context"],
        hypotheses=state.get("hypotheses"),
        previous_assessment=state.get("missing_information"),
    )
    usage = state["budget_usage"]
    return {
        "workflow_state": InvestigationState.ASSESSING_GAPS,
        "missing_information": assessment,
        "budget_usage": BudgetUsage(
            rounds=max(usage.rounds, state["round_number"]),
            queries=usage.queries,
            reasoning_calls=usage.reasoning_calls + 1,
            input_units=usage.input_units,
            output_units=usage.output_units,
        ),
        "progress_events": progress_update(
            state,
            dependencies,
            kind="reasoning",
            stage="assess_gaps",
            title="Information gaps assessed",
            metadata={"round_number": state["round_number"]},
        ),
    }
