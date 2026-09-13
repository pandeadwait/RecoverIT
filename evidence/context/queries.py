"""Database-agnostic context and evidence reads exposed to Person 3."""

from __future__ import annotations

from typing import Protocol

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceFilter, EvidenceRecord
from evidence.context.repositories.ports import ContextRepository
from evidence.repositories.ports import EvidenceRepository


class IncidentContextReader(Protocol):
    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...

    def query_evidence(
        self, evidence_filter: EvidenceFilter
    ) -> tuple[EvidenceRecord, ...]: ...

    def get_latest_context(
        self, incident_id: str
    ) -> IncidentContextSnapshot | None: ...


class EvidenceContextQueryService(IncidentContextReader):
    """Exposes domain contracts without leaking repository implementation APIs."""

    def __init__(
        self,
        evidence_repository: EvidenceRepository,
        context_repository: ContextRepository,
    ) -> None:
        self._evidence = evidence_repository
        self._contexts = context_repository

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self._evidence.get(evidence_id)

    def query_evidence(
        self, evidence_filter: EvidenceFilter
    ) -> tuple[EvidenceRecord, ...]:
        return self._evidence.query(evidence_filter)

    def get_latest_context(self, incident_id: str) -> IncidentContextSnapshot | None:
        return self._contexts.get_latest(incident_id)
