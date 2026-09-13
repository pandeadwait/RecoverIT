"""Thread-safe in-memory deduplication-state repository."""

from __future__ import annotations

from contracts.common import canonical_bytes
from evidence.deduplication.models import ProvenanceAttachment, RepeatedEventAggregate
from evidence.deduplication.repositories.ports import DeduplicationStateRepository
from evidence.repositories.ports import (
    RepositoryConflictError,
    RepositoryValidationError,
)
from evidence.repositories.state import InMemoryRepositoryState


class InMemoryDeduplicationStateRepository(DeduplicationStateRepository):
    def __init__(self, state: InMemoryRepositoryState) -> None:
        self._state = state

    def get_aggregates(
        self, evidence_ids: tuple[str, ...]
    ) -> tuple[RepeatedEventAggregate, ...]:
        selected = set(evidence_ids)
        with self._state.lock:
            return tuple(
                self._state.aggregates[evidence_id]
                for evidence_id in sorted(selected)
                if evidence_id in self._state.aggregates
            )

    def get_attachments(
        self, evidence_ids: tuple[str, ...]
    ) -> tuple[ProvenanceAttachment, ...]:
        selected = set(evidence_ids)
        with self._state.lock:
            values = (
                attachment
                for attachment in self._state.provenance_attachments.values()
                if attachment.evidence_id in selected
            )
            return tuple(sorted(values, key=canonical_bytes))

    def save_all(
        self,
        aggregates: tuple[RepeatedEventAggregate, ...],
        provenance_attachments: tuple[ProvenanceAttachment, ...],
    ) -> None:
        with self._state.lock:
            known_evidence = set(self._state.evidence)
            referenced = {item.evidence_id for item in aggregates}
            referenced.update(item.evidence_id for item in provenance_attachments)
            missing = sorted(referenced - known_evidence)
            if missing:
                raise RepositoryValidationError(
                    "deduplication state references unknown evidence",
                    details={"evidence_ids": missing},
                )

            pending_aggregates = dict(self._state.aggregates)
            for aggregate in aggregates:
                existing = pending_aggregates.get(aggregate.evidence_id)
                if existing is not None:
                    self._validate_update(existing, aggregate)
                pending_aggregates[aggregate.evidence_id] = aggregate

            pending_attachments = dict(self._state.provenance_attachments)
            for attachment in provenance_attachments:
                key = (attachment.evidence_id, canonical_bytes(attachment.provenance))
                pending_attachments.setdefault(key, attachment)

            self._state.aggregates = pending_aggregates
            self._state.provenance_attachments = pending_attachments

    @staticmethod
    def _validate_update(
        existing: RepeatedEventAggregate, incoming: RepeatedEventAggregate
    ) -> None:
        identity_fields = (
            "aggregation_key_hash",
            "source_type",
            "service",
            "bucket_start",
        )
        if any(
            getattr(existing, name) != getattr(incoming, name)
            for name in identity_fields
        ):
            raise RepositoryConflictError(
                "aggregation identity cannot be changed",
                details={"evidence_id": incoming.evidence_id},
            )
        if incoming.occurrence_count < existing.occurrence_count:
            raise RepositoryConflictError(
                "aggregation occurrence count cannot decrease",
                details={"evidence_id": incoming.evidence_id},
            )
        existing_provenance = {
            canonical_bytes(item) for item in existing.contributing_provenance
        }
        incoming_provenance = {
            canonical_bytes(item) for item in incoming.contributing_provenance
        }
        if not existing_provenance.issubset(incoming_provenance):
            raise RepositoryConflictError(
                "aggregation provenance cannot be removed",
                details={"evidence_id": incoming.evidence_id},
            )
