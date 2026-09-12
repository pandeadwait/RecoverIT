"""Immutable, safe audit values for deterministic deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from contracts.common import (
    datetime_to_wire,
    require_identifier,
    require_int,
    require_string,
)
from contracts.evidence import EvidenceProvenance
from evidence.security.models import SecuredEvidenceCandidate


class DeduplicationAction(StrEnum):
    CREATE = "create"
    REUSE = "reuse"
    AGGREGATE = "aggregate"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class IdentifiedEvidenceCandidate:
    evidence_id: str
    candidate: SecuredEvidenceCandidate

    def __post_init__(self) -> None:
        require_identifier(self.evidence_id, "evidence_id")

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "candidate": self.candidate.to_dict()}


@dataclass(frozen=True, slots=True)
class ProvenanceAttachment:
    evidence_id: str
    provenance: EvidenceProvenance
    reason: str

    def __post_init__(self) -> None:
        require_identifier(self.evidence_id, "evidence_id")
        require_string(self.reason, "reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "provenance": self.provenance.to_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RepeatedEventAggregate:
    """Sidecar aggregate; it never rewrites immutable evidence content."""

    evidence_id: str
    aggregation_key_hash: str
    source_type: str
    service: str
    bucket_start: datetime
    occurrence_count: int
    first_event_time: datetime | None
    last_event_time: datetime | None
    contributing_provenance: tuple[EvidenceProvenance, ...]
    truncated_source: bool

    def __post_init__(self) -> None:
        require_identifier(self.evidence_id, "evidence_id")
        require_string(self.aggregation_key_hash, "aggregation_key_hash")
        require_string(self.source_type, "source_type")
        require_string(self.service, "service")
        require_int(self.occurrence_count, "occurrence_count", minimum=1)
        if self.bucket_start.tzinfo is None or self.bucket_start.utcoffset() is None:
            raise ValueError("bucket_start must include timezone information")
        for name in ("first_event_time", "last_event_time"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include timezone information")
        if self.first_event_time is not None and self.last_event_time is not None:
            if self.first_event_time > self.last_event_time:
                raise ValueError("first_event_time must not be after last_event_time")
        if not self.contributing_provenance:
            raise ValueError("contributing_provenance must not be empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "aggregation_key_hash": self.aggregation_key_hash,
            "source_type": self.source_type,
            "service": self.service,
            "bucket_start": datetime_to_wire(self.bucket_start),
            "occurrence_count": self.occurrence_count,
            "contributing_provenance": [
                value.to_dict() for value in self.contributing_provenance
            ],
            "truncated_source": self.truncated_source,
        }
        if self.first_event_time is not None:
            result["first_event_time"] = datetime_to_wire(self.first_event_time)
        if self.last_event_time is not None:
            result["last_event_time"] = datetime_to_wire(self.last_event_time)
        return result


@dataclass(frozen=True, slots=True)
class DeduplicationDecision:
    action: DeduplicationAction
    evidence_id: str | None
    reason: str
    provenance: EvidenceProvenance
    aggregation_key_hash: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_id is not None:
            require_identifier(self.evidence_id, "evidence_id")
        require_string(self.reason, "reason")
        if self.aggregation_key_hash is not None:
            require_string(self.aggregation_key_hash, "aggregation_key_hash")
        if self.action is DeduplicationAction.REJECT and self.evidence_id is not None:
            raise ValueError("rejected decisions must not provide evidence_id")
        if self.action is not DeduplicationAction.REJECT and self.evidence_id is None:
            raise ValueError("non-rejected decisions require evidence_id")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action.value,
            "reason": self.reason,
            "provenance": self.provenance.to_dict(),
        }
        if self.evidence_id is not None:
            result["evidence_id"] = self.evidence_id
        if self.aggregation_key_hash is not None:
            result["aggregation_key_hash"] = self.aggregation_key_hash
        return result


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    decisions: tuple[DeduplicationDecision, ...]
    creates: tuple[IdentifiedEvidenceCandidate, ...]
    aggregates: tuple[RepeatedEventAggregate, ...]
    provenance_attachments: tuple[ProvenanceAttachment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [decision.to_dict() for decision in self.decisions],
            "creates": [candidate.to_dict() for candidate in self.creates],
            "aggregates": [aggregate.to_dict() for aggregate in self.aggregates],
            "provenance_attachments": [
                attachment.to_dict() for attachment in self.provenance_attachments
            ],
        }
