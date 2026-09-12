"""Atomic repository coordinator port and in-memory reference implementation."""

from __future__ import annotations

from typing import Protocol

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from evidence.context.repositories.memory import InMemoryContextRepository
from evidence.context.repositories.ports import ContextRepository
from evidence.repositories.memory import InMemoryEvidenceRepository
from evidence.repositories.ports import EvidenceRepository, RepositoryValidationError
from evidence.repositories.state import InMemoryRepositoryState
from timeline.repositories.memory import InMemoryTimelineRepository
from timeline.repositories.ports import TimelineRepository


class RepositoryCoordinator(Protocol):
    evidence: EvidenceRepository
    timelines: TimelineRepository
    contexts: ContextRepository

    def save_revision(
        self,
        records: tuple[EvidenceRecord, ...],
        timeline_revision: int,
        timeline: Timeline,
        snapshot: IncidentContextSnapshot,
    ) -> str: ...


class InMemoryRepositoryCoordinator(RepositoryCoordinator):
    """Commits evidence, timeline, and context together or restores all state."""

    def __init__(self) -> None:
        self._state = InMemoryRepositoryState()
        self.evidence = InMemoryEvidenceRepository(self._state)
        self.timelines = InMemoryTimelineRepository(self._state)
        self.contexts = InMemoryContextRepository(self._state)

    def save_revision(
        self,
        records: tuple[EvidenceRecord, ...],
        timeline_revision: int,
        timeline: Timeline,
        snapshot: IncidentContextSnapshot,
    ) -> str:
        if timeline.incident_id != snapshot.incident_id:
            raise RepositoryValidationError("timeline and snapshot incidents must match")
        if timeline_revision != snapshot.revision:
            raise RepositoryValidationError("timeline and snapshot revisions must match")
        if any(record.incident_id != snapshot.incident_id for record in records):
            raise RepositoryValidationError("all evidence must belong to the snapshot incident")

        with self._state.lock:
            evidence_before = dict(self._state.evidence)
            timelines_before = dict(self._state.timelines)
            contexts_before = dict(self._state.contexts)
            revisions_before = dict(self._state.context_revisions)
            try:
                self.evidence.save_all(records)
                self.timelines.replace_revision(
                    snapshot.incident_id,
                    timeline_revision,
                    timeline.events,
                    timeline.relationships,
                )
                return self.contexts.save(snapshot)
            except Exception:
                self._state.evidence = evidence_before
                self._state.timelines = timelines_before
                self._state.contexts = contexts_before
                self._state.context_revisions = revisions_before
                raise
