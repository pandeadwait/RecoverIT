"""Context publication orchestration over domain-owned repository ports."""

from __future__ import annotations

from typing import Protocol

from contracts.collection import RawEvidenceBatch
from contracts.common import SCHEMA_VERSION
from contracts.context import IncidentContextSnapshot
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.incident import IncidentSeed
from contracts.timeline import Timeline
from evidence.context.builder import ContextBuildError, ContextSnapshotBuilder
from evidence.context.coverage import SourceCoverageCalculator
from evidence.context.repositories.ports import ContextRepository
from evidence.repositories.ports import (
    EvidenceOrder,
    EvidencePageRequest,
    EvidenceRepository,
)
from timeline.repositories.ports import TimelineRepository


class ContextPublicationRepository(Protocol):
    """Minimal atomic persistence boundary needed for context publication."""

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


class ContextPublicationService:
    """Builds and atomically persists the context revision consumed by Person 3."""

    def __init__(
        self,
        coordinator: ContextPublicationRepository,
        *,
        builder: ContextSnapshotBuilder | None = None,
        coverage: SourceCoverageCalculator | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._builder = builder or ContextSnapshotBuilder()
        self._coverage = coverage or SourceCoverageCalculator()

    def publish(
        self,
        incident: IncidentSeed,
        batches: tuple[RawEvidenceBatch, ...],
        warnings: tuple[ProcessingWarning, ...] = (),
        previous_snapshot: IncidentContextSnapshot | None = None,
    ) -> IncidentContextSnapshot:
        if any(batch.incident_id != incident.incident_id for batch in batches):
            raise ContextBuildError("collection batch belongs to another incident")
        records = self._load_all_evidence(incident.incident_id)
        timeline = self._coordinator.timelines.get_latest(incident.incident_id)
        if timeline is None:
            raise ContextBuildError("no persisted timeline exists for the incident")
        persisted_previous = self._coordinator.contexts.get_latest(incident.incident_id)
        if (
            previous_snapshot is not None
            and persisted_previous is not None
            and previous_snapshot.snapshot_id != persisted_previous.snapshot_id
        ):
            raise ContextBuildError("previous snapshot is not the latest persisted revision")
        previous = persisted_previous or previous_snapshot
        source_coverage = self._coverage.calculate(
            batches,
            previous.source_coverage if previous is not None else None,
        )
        snapshot = self._builder.build(
            incident,
            records,
            timeline,
            source_coverage,
            warnings,
            previous,
        )
        self._coordinator.save_revision(
            records,
            snapshot.revision,
            timeline,
            snapshot,
        )
        return snapshot

    def _load_all_evidence(self, incident_id: str) -> tuple[EvidenceRecord, ...]:
        evidence_filter = EvidenceFilter(
            schema_version=SCHEMA_VERSION,
            incident_id=incident_id,
            include_unknown_event_time=True,
            limit=2_147_483_647,
        )
        records: list[EvidenceRecord] = []
        cursor: str | None = None
        while True:
            page = self._coordinator.evidence.query_page(
                evidence_filter,
                EvidencePageRequest(
                    limit=1000,
                    cursor=cursor,
                    order=EvidenceOrder.EVENT_TIME_ASC,
                ),
            )
            records.extend(page.items)
            if page.next_cursor is None:
                return tuple(records)
            cursor = page.next_cursor
