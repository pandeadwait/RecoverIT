"""Application-level results for evidence ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.common import SCHEMA_VERSION, require_identifier
from contracts.errors import ProcessingError, ProcessingWarning
from evidence.deduplication.models import (
    DeduplicationDecision,
    ProvenanceAttachment,
    RepeatedEventAggregate,
)
from evidence.security.models import QuarantinedRecord


class EvidenceProcessingError(RuntimeError):
    """Fatal ingestion failure represented by a portable processing error."""

    def __init__(self, error: ProcessingError) -> None:
        self.error = error
        super().__init__(error.message)


@dataclass(frozen=True, slots=True)
class EvidenceProcessingResult:
    incident_id: str
    batch_ids: tuple[str, ...]
    saved_evidence_ids: tuple[str, ...]
    reused_evidence_ids: tuple[str, ...]
    aggregated_evidence_ids: tuple[str, ...]
    warnings: tuple[ProcessingWarning, ...]
    source_errors: tuple[ProcessingError, ...]
    quarantined: tuple[QuarantinedRecord, ...]
    decisions: tuple[DeduplicationDecision, ...]
    aggregates: tuple[RepeatedEventAggregate, ...]
    provenance_attachments: tuple[ProvenanceAttachment, ...]

    def __post_init__(self) -> None:
        require_identifier(self.incident_id, "incident_id")
        for batch_id in self.batch_ids:
            require_identifier(batch_id, "batch_ids")
        for name in (
            "saved_evidence_ids",
            "reused_evidence_ids",
            "aggregated_evidence_ids",
        ):
            for evidence_id in getattr(self, name):
                require_identifier(evidence_id, name)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.saved_evidence_ids)
                | set(self.reused_evidence_ids)
                | set(self.aggregated_evidence_ids)
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "incident_id": self.incident_id,
            "batch_ids": list(self.batch_ids),
            "saved_evidence_ids": list(self.saved_evidence_ids),
            "reused_evidence_ids": list(self.reused_evidence_ids),
            "aggregated_evidence_ids": list(self.aggregated_evidence_ids),
            "evidence_ids": list(self.evidence_ids),
            "warnings": [item.to_dict() for item in self.warnings],
            "source_errors": [item.to_dict() for item in self.source_errors],
            "quarantined": [item.to_dict() for item in self.quarantined],
            "decisions": [item.to_dict() for item in self.decisions],
            "aggregates": [item.to_dict() for item in self.aggregates],
            "provenance_attachments": [
                item.to_dict() for item in self.provenance_attachments
            ],
        }
