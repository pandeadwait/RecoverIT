"""Persistence port for mutable deduplication sidecars and provenance links."""

from __future__ import annotations

from typing import Protocol

from evidence.deduplication.models import ProvenanceAttachment, RepeatedEventAggregate


class DeduplicationStateRepository(Protocol):
    """Stores aggregation state separately from immutable evidence records."""

    def get_aggregates(
        self, evidence_ids: tuple[str, ...]
    ) -> tuple[RepeatedEventAggregate, ...]: ...

    def get_attachments(
        self, evidence_ids: tuple[str, ...]
    ) -> tuple[ProvenanceAttachment, ...]: ...

    def save_all(
        self,
        aggregates: tuple[RepeatedEventAggregate, ...],
        provenance_attachments: tuple[ProvenanceAttachment, ...],
    ) -> None: ...
