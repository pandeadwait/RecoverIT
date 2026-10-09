"""Execute a validated query plan through the collection boundary."""

from __future__ import annotations

from contracts.enums import InvestigationState
from contracts.investigation.schemas import BudgetUsage
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def collect_evidence(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    plan = state.get("query_plan")
    if plan is None or not plan.queries:
        raise ValueError("collect_evidence requires a non-empty query plan")
    batch = await dependencies.collection_service.collect(
        plan=plan,
        capabilities=state["source_capabilities"],
    )
    usage = state["budget_usage"]
    return {
        "workflow_state": InvestigationState.COLLECTING_EVIDENCE,
        "latest_batch": batch,
        "batch_history": [*state.get("batch_history", []), batch],
        "budget_usage": BudgetUsage(
            rounds=usage.rounds,
            queries=usage.queries + len(plan.queries),
            reasoning_calls=usage.reasoning_calls,
            input_units=usage.input_units,
            output_units=usage.output_units,
        ),
        "progress_events": progress_update(
            state,
            dependencies,
            kind="action",
            stage="collect_evidence",
            title="Evidence collection completed",
            metadata={"result_count": len(batch.results)},
        ),
    }
