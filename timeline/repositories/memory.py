"""Thread-safe in-memory reference timeline repository."""

from __future__ import annotations

from contracts.common import require_identifier, require_int
from contracts.timeline import TemporalRelationship, Timeline, TimelineEvent
from evidence.repositories.ports import RepositoryValidationError
from evidence.repositories.state import InMemoryRepositoryState
from timeline.repositories.ports import TimelineRepository


def _ordered_timeline(timeline: Timeline) -> Timeline:
    known = [event for event in timeline.events if event.event_time is not None]
    unknown = [event for event in timeline.events if event.event_time is None]
    known.sort(key=lambda event: (event.event_time, event.timeline_event_id))
    unknown.sort(key=lambda event: event.timeline_event_id)
    relationships = tuple(sorted(timeline.relationships, key=lambda item: item.relationship_id))
    return Timeline(
        incident_id=timeline.incident_id,
        events=tuple(known + unknown),
        relationships=relationships,
    )


class InMemoryTimelineRepository(TimelineRepository):
    def __init__(self, state: InMemoryRepositoryState | None = None) -> None:
        self._state = state or InMemoryRepositoryState()

    def replace_revision(
        self,
        incident_id: str,
        revision: int,
        events: tuple[TimelineEvent, ...],
        relationships: tuple[TemporalRelationship, ...],
    ) -> None:
        require_identifier(incident_id, "incident_id")
        require_int(revision, "revision", minimum=1)
        try:
            timeline = Timeline(
                incident_id=incident_id,
                events=tuple(events),
                relationships=tuple(relationships),
            )
        except ValueError as error:
            raise RepositoryValidationError(
                "timeline content does not match the repository incident"
            ) from error
        ordered = _ordered_timeline(timeline)
        with self._state.lock:
            self._state.timelines[(incident_id, revision)] = ordered

    def get_latest(self, incident_id: str) -> Timeline | None:
        require_identifier(incident_id, "incident_id")
        with self._state.lock:
            revisions = [
                revision
                for stored_incident, revision in self._state.timelines
                if stored_incident == incident_id
            ]
            if not revisions:
                return None
            return self._state.timelines[(incident_id, max(revisions))]

    def get_revision(self, incident_id: str, revision: int) -> Timeline | None:
        require_identifier(incident_id, "incident_id")
        require_int(revision, "revision", minimum=1)
        with self._state.lock:
            return self._state.timelines.get((incident_id, revision))
