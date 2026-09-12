"""Atomic persistence coordinator port used by application services."""

from __future__ import annotations

from typing import Protocol

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from evidence.context.repositories.ports import ContextRepository
from evidence.deduplication.models import ProvenanceAttachment, RepeatedEventAggregate
from evidence.deduplication.repositories.ports import DeduplicationStateRepository
from evidence.repositories.ports import EvidenceRepository
from timeline.repositories.ports import TimelineRepository


class EvidenceBatchCoordinator(Protocol):
    evidence: EvidenceRepository
    deduplication: DeduplicationStateRepository

    def save_evidence_batch(
        self,
        records: tuple[EvidenceRecord, ...],
        aggregates: tuple[RepeatedEventAggregate, ...],
        provenance_attachments: tuple[ProvenanceAttachment, ...],
    ) -> tuple[str, ...]: ...


class RepositoryCoordinator(EvidenceBatchCoordinator, Protocol):
    timelines: TimelineRepository
    contexts: ContextRepository

    def save_revision(
        self,
        records: tuple[EvidenceRecord, ...],
        timeline_revision: int,
        timeline: Timeline,
        snapshot: IncidentContextSnapshot,
    ) -> str: ...
