"""Safe values produced by the Phase 4 security boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from contracts.common import (
    SCHEMA_VERSION,
    freeze_json,
    require_identifier,
    require_mapping,
    require_schema_version,
    require_string,
    thaw_json,
)
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.evidence import EvidenceProvenance, EvidenceQuality
from evidence.normalization.models import NormalizedTimestamps


class RedactionOutcome(StrEnum):
    PASS = "pass"
    REDACT = "redact"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True, repr=False)
class RedactionResult:
    outcome: RedactionOutcome
    value: Any
    redacted_paths: tuple[str, ...]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", freeze_json(self.value, "redaction.value"))

    @property
    def redactions_applied(self) -> bool:
        return bool(self.redacted_paths)

    def __repr__(self) -> str:
        return (
            f"RedactionResult(outcome={self.outcome.value!r}, "
            f"redaction_count={len(self.redacted_paths)})"
        )


@dataclass(frozen=True, slots=True)
class QuarantinedRecord:
    schema_version: str
    incident_id: str
    batch_id: str
    query_id: str
    source_record_id: str | None
    raw_payload_hash: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("QuarantinedRecord requires schema version 1.0")
        for name in ("incident_id", "batch_id", "query_id"):
            require_identifier(getattr(self, name), name)
        if self.source_record_id is not None:
            require_identifier(self.source_record_id, "source_record_id")
        require_string(self.raw_payload_hash, "raw_payload_hash")
        require_string(self.reason_code, "reason_code")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "batch_id": self.batch_id,
            "query_id": self.query_id,
            "raw_payload_hash": self.raw_payload_hash,
            "reason_code": self.reason_code,
        }
        if self.source_record_id is not None:
            result["source_record_id"] = self.source_record_id
        return result

    @classmethod
    def from_dict(cls, value: object) -> "QuarantinedRecord":
        data = require_mapping(value, "quarantined_record")
        return cls(
            schema_version=require_schema_version(data, "quarantined_record.schema_version"),
            incident_id=require_identifier(
                data.get("incident_id"), "quarantined_record.incident_id"
            ),
            batch_id=require_identifier(data.get("batch_id"), "quarantined_record.batch_id"),
            query_id=require_identifier(data.get("query_id"), "quarantined_record.query_id"),
            source_record_id=(
                None
                if data.get("source_record_id") is None
                else require_identifier(
                    data.get("source_record_id"), "quarantined_record.source_record_id"
                )
            ),
            raw_payload_hash=require_string(
                data.get("raw_payload_hash"), "quarantined_record.raw_payload_hash"
            ),
            reason_code=require_string(
                data.get("reason_code"), "quarantined_record.reason_code"
            ),
        )


@dataclass(frozen=True, slots=True)
class SecuredEvidenceCandidate:
    """Policy-approved candidate containing no original raw payload."""

    schema_version: str
    incident_id: str
    source_type: str
    source_status: str
    evidence_type: str
    service: str
    resource: str | None
    timestamps: NormalizedTimestamps
    summary: str
    attributes: Any
    provenance: EvidenceProvenance
    quality: EvidenceQuality
    quality_rationale: str
    warnings: tuple[ProcessingWarning, ...]
    redacted_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("SecuredEvidenceCandidate requires schema version 1.0")
        require_identifier(self.incident_id, "incident_id")
        for name in (
            "source_type",
            "source_status",
            "evidence_type",
            "service",
            "summary",
            "quality_rationale",
        ):
            require_string(getattr(self, name), name)
        if self.resource is not None:
            require_string(self.resource, "resource")
        object.__setattr__(self, "attributes", freeze_json(self.attributes, "attributes"))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "source_type": self.source_type,
            "source_status": self.source_status,
            "evidence_type": self.evidence_type,
            "service": self.service,
            "timestamps": self.timestamps.to_dict(),
            "summary": self.summary,
            "attributes": thaw_json(self.attributes),
            "provenance": self.provenance.to_dict(),
            "quality": self.quality.to_dict(),
            "quality_rationale": self.quality_rationale,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "redacted_paths": list(self.redacted_paths),
        }
        if self.resource is not None:
            result["resource"] = self.resource
        return result


@dataclass(frozen=True, slots=True)
class SecurityProcessingResult:
    approved: tuple[SecuredEvidenceCandidate, ...]
    quarantined: tuple[QuarantinedRecord, ...]
    warnings: tuple[ProcessingWarning, ...]
    errors: tuple[ProcessingError, ...] = ()
