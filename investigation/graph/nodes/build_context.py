"""Normalize evidence and publish the next context revision."""

from __future__ import annotations

from contracts.enums import InvestigationState
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def build_context(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    batch = state.get("latest_batch")
    if batch is None:
        raise ValueError("collect_evidence must set latest_batch before context building")
    context = await dependencies.context_builder.build(
        incident=state["incident"],
        batch=batch,
        previous_context=state["context"],
    )
    return {
        "workflow_state": InvestigationState.BUILDING_TIMELINE,
        "context": context,
        "progress_events": progress_update(
            state,
            dependencies,
            kind="observation",
            stage="build_context",
            title="Evidence context updated",
            metadata={"revision": context.revision},
        ),
    }
