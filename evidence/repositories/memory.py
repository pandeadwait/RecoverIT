"""Thread-safe in-memory reference adapter for the evidence repository port."""

from __future__ import annotations

from contracts.common import canonical_bytes, require_identifier
from contracts.evidence import EvidenceFilter, EvidenceRecord
from evidence.repositories._ordering import decode_cursor, encode_cursor, matches, sort_records
from evidence.repositories.ports import (
    EvidenceOrder,
    EvidencePage,
    EvidencePageRequest,
    EvidenceRepository,
    RepositoryConflictError,
)
from evidence.repositories.state import InMemoryRepositoryState


class InMemoryEvidenceRepository(EvidenceRepository):
    def __init__(self, state: InMemoryRepositoryState | None = None) -> None:
        self._state = state or InMemoryRepositoryState()

    def save_all(self, records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
        records = tuple(records)
        with self._state.lock:
            pending: dict[str, EvidenceRecord] = {}
            for record in records:
                existing = pending.get(record.evidence_id) or self._state.evidence.get(record.evidence_id)
                if existing is not None and canonical_bytes(existing) != canonical_bytes(record):
                    raise RepositoryConflictError(
                        f"evidence ID {record.evidence_id!r} already stores different immutable content",
                        details={"evidence_id": record.evidence_id},
                    )
                pending[record.evidence_id] = record
            self._state.evidence.update(pending)
            return tuple(record.evidence_id for record in records)

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        require_identifier(evidence_id, "evidence_id")
        with self._state.lock:
            return self._state.evidence.get(evidence_id)

    def query(self, evidence_filter: EvidenceFilter) -> tuple[EvidenceRecord, ...]:
        page = self.query_page(
            evidence_filter,
            EvidencePageRequest(limit=evidence_filter.limit),
        )
        return page.items

    def query_page(
        self, evidence_filter: EvidenceFilter, page: EvidencePageRequest
    ) -> EvidencePage:
        with self._state.lock:
            filtered = [
                record
                for record in self._state.evidence.values()
                if matches(record, evidence_filter)
            ]
        ordered = sort_records(filtered, page.order)
        ordered = ordered[: evidence_filter.limit]
        offset = decode_cursor(page.cursor, page.order)
        effective_limit = min(page.limit, evidence_filter.limit)
        items = tuple(ordered[offset : offset + effective_limit])
        next_offset = offset + len(items)
        next_cursor = (
            encode_cursor(page.order, next_offset) if next_offset < len(ordered) else None
        )
        return EvidencePage(items=items, next_cursor=next_cursor)
