"""Source-neutral values produced and consumed within normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contracts.common import (
    SCHEMA_VERSION,
    datetime_to_wire,
    freeze_json,
    require_bool,
    require_identifier,
    require_int,
    require_string,
    thaw_json,
)
from contracts.errors import ProcessingError, ProcessingWarning


class BatchNormalizationError(ValueError):
    """A rejected batch envelope represented by the portable error contract."""

    def __init__(self, error: ProcessingError) -> None:
        self.error = error
        super().__init__(error.message)


class RecordNormalizationError(ValueError):
    """A single raw record cannot be classified safely."""


@dataclass(frozen=True, slots=True)
class NormalizedTimestamps:
    event_time: datetime | None
    observed_at: datetime | None
    collected_at: datetime
    event_time_original: str | None
    observed_at_original: str | None
    event_time_offset: str | None
    observed_at_offset: str | None
    event_time_approximate: bool = False
    time_uncertainty_ms: int | None = None

    def __post_init__(self) -> None:
        for name in ("event_time", "observed_at", "collected_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include timezone information")
        require_bool(self.event_time_approximate, "event_time_approximate")
        if self.time_uncertainty_ms is not None:
            require_int(self.time_uncertainty_ms, "time_uncertainty_ms", minimum=0)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "collected_at": datetime_to_wire(self.collected_at),
            "event_time_approximate": self.event_time_approximate,
        }
        for name in ("event_time", "observed_at"):
            value = getattr(self, name)
            if value is not None:
                result[name] = datetime_to_wire(value)
        for name in (
            "event_time_original",
            "observed_at_original",
            "event_time_offset",
            "observed_at_offset",
            "time_uncertainty_ms",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass(frozen=True, slots=True, repr=False)
class NormalizedEvidenceCandidate:
    """Canonical pre-security candidate; never a persistence DTO.

    The raw payload remains in memory only so Phase 4 can hash and redact it.
    It is excluded from repr and from ``to_dict`` by design.
    """

    schema_version: str
    incident_id: str
    batch_id: str
    query_id: str
    source_record_id: str | None
    source_adapter: str
    source_type: str
    source_status: str
    source_truncated: bool
    evidence_type: str
    service: str
    resource: str | None
    timestamps: NormalizedTimestamps
    summary: str
    attributes: Any
    warnings: tuple[ProcessingWarning, ...] = ()
    raw_payload: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("NormalizedEvidenceCandidate requires schema version 1.0")
        for name in ("incident_id", "batch_id", "query_id"):
            require_identifier(getattr(self, name), name)
        if self.source_record_id is not None:
            require_identifier(self.source_record_id, "source_record_id")
        for name in (
            "source_adapter",
            "source_type",
            "source_status",
            "evidence_type",
            "service",
            "summary",
        ):
            require_string(getattr(self, name), name)
        if self.resource is not None:
            require_string(self.resource, "resource")
        require_bool(self.source_truncated, "source_truncated")
        object.__setattr__(self, "attributes", freeze_json(self.attributes, "attributes"))
        object.__setattr__(self, "raw_payload", freeze_json(self.raw_payload, "raw_payload"))

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic projection that omits the original raw payload."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "batch_id": self.batch_id,
            "query_id": self.query_id,
            "source_adapter": self.source_adapter,
            "source_type": self.source_type,
            "source_status": self.source_status,
            "source_truncated": self.source_truncated,
            "evidence_type": self.evidence_type,
            "service": self.service,
            "timestamps": self.timestamps.to_dict(),
            "summary": self.summary,
            "attributes": thaw_json(self.attributes),
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
        if self.source_record_id is not None:
            result["source_record_id"] = self.source_record_id
        if self.resource is not None:
            result["resource"] = self.resource
        return result

    def __repr__(self) -> str:
        return (
            "NormalizedEvidenceCandidate("
            f"incident_id={self.incident_id!r}, batch_id={self.batch_id!r}, "
            f"query_id={self.query_id!r})"
        )


@dataclass(frozen=True, slots=True)
class BatchNormalizationResult:
    incident_id: str
    batch_id: str
    candidates: tuple[NormalizedEvidenceCandidate, ...]
    warnings: tuple[ProcessingWarning, ...]
    errors: tuple[ProcessingError, ...]

    def __post_init__(self) -> None:
        require_identifier(self.incident_id, "incident_id")
        require_identifier(self.batch_id, "batch_id")
