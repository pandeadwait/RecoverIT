"""Small shared helpers for graph nodes."""

from __future__ import annotations

from uuid import uuid4

from contracts.errors.schemas import ProgressEvent
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.state import InvestigationGraphState


def progress_update(
    state: InvestigationGraphState,
    dependencies: GraphDependencies,
    *,
    kind: str,
    stage: str,
    title: str,
    detail: str = "",
    output: str = "",
    metadata: dict[str, object] | None = None,
) -> list[ProgressEvent]:
    """Append and publish one display-safe progress event."""

    event = ProgressEvent(
        event_id=f"evt-{uuid4().hex}",
        incident_id=state["incident"].incident_id,
        kind=kind,
        stage=stage,
        title=title,
        detail=detail,
        output=output,
        metadata=dict(metadata or {}),
        created_at=dependencies.clock.now(),
    )
    dependencies.progress_sink.emit(event)
    return [*state.get("progress_events", []), event]
