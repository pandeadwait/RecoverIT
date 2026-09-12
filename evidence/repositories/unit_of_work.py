"""Atomic repository coordinator port and in-memory reference implementation."""

from __future__ import annotations

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from evidence.context.repositories.memory import InMemoryContextRepository
from evidence.deduplication.models import ProvenanceAttachment, RepeatedEventAggregate
from evidence.deduplication.repositories.memory import InMemoryDeduplicationStateRepository
from evidence.repositories.coordinator import RepositoryCoordinator
from evidence.repositories.memory import InMemoryEvidenceRepository
from evidence.repositories.ports import RepositoryValidationError
from evidence.repositories.state import InMemoryRepositoryState
from timeline.repositories.memory import InMemoryTimelineRepository


class InMemoryRepositoryCoordinator(RepositoryCoordinator):
    """Commits evidence, timeline, and context together or restores all state."""

    def __init__(self) -> None:
        self._state = InMemoryRepositoryState()
        self.evidence = InMemoryEvidenceRepository(self._state)
        self.deduplication = InMemoryDeduplicationStateRepository(self._state)
        self.timelines = InMemoryTimelineRepository(self._state)
        self.contexts = InMemoryContextRepository(self._state)

    def save_evidence_batch(
        self,
        records: tuple[EvidenceRecord, ...],
        aggregates: tuple[RepeatedEventAggregate, ...],
        provenance_attachments: tuple[ProvenanceAttachment, ...],
    ) -> tuple[str, ...]:
        with self._state.lock:
            evidence_before = dict(self._state.evidence)
            aggregates_before = dict(self._state.aggregates)
            attachments_before = dict(self._state.provenance_attachments)
            try:
                saved_ids = self.evidence.save_all(records)
                self.deduplication.save_all(aggregates, provenance_attachments)
                return saved_ids
            except Exception:
                self._state.evidence = evidence_before
                self._state.aggregates = aggregates_before
                self._state.provenance_attachments = attachments_before
                raise

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
            aggregates_before = dict(self._state.aggregates)
            attachments_before = dict(self._state.provenance_attachments)
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
                self._state.aggregates = aggregates_before
                self._state.provenance_attachments = attachments_before
                raise
