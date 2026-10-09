"""Initialize checkpoint-safe investigation state."""

from __future__ import annotations

from contracts.enums import InvestigationState, SourceCoverageStatus, SourceType
from contracts.evidence.schemas import IncidentContextSnapshot, IncidentSummary
from contracts.investigation.schemas import BudgetUsage
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.common import progress_update
from investigation.graph.state import InvestigationGraphState


async def initialize(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
) -> dict[str, object]:
    incident = state["incident"]
    created_at = dependencies.clock.now()
    context = IncidentContextSnapshot(
        snapshot_id=f"ctx-{incident.incident_id}-0",
        incident_id=incident.incident_id,
        revision=0,
        created_at=created_at,
        incident=IncidentSummary(
            service=incident.service,
            environment=incident.environment,
            severity=incident.severity,
            detected_at=incident.detected_at,
            summary=incident.summary,
        ),
        source_coverage={
            source.value: SourceCoverageStatus.NOT_QUERIED for source in SourceType
        },
    )
    return {
        "workflow_state": InvestigationState.RECEIVED,
        "started_at": created_at,
        "round_number": 1,
        "budget_usage": BudgetUsage(),
        "missing_information": None,
        "query_plan": None,
        "query_history": [],
        "latest_batch": None,
        "batch_history": [],
        "context": context,
        "hypotheses": None,
        "stop_decision": None,
        "ranked_result": None,
        "errors": [],
        "progress_events": progress_update(
            state,
            dependencies,
            kind="status",
            stage="initialize",
            title="Investigation initialized",
        ),
    }
