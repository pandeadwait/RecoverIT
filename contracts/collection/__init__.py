"""Source-neutral collection contracts produced by Person 1 and consumed by Person 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from contracts.common import SCHEMA_VERSION, datetime_to_wire, freeze_json, parse_datetime, require_bool, require_extensible_code, require_identifier, require_list, require_mapping, require_optional_datetime, require_schema_version, require_string, thaw_json
from contracts.errors import ProcessingError, ProcessingWarning

_SOURCE_TYPES = {"logs", "metrics", "changes", "deployments", "pipelines", "configuration"}
_SOURCE_STATUSES = {"ok", "partial", "unavailable", "error"}


@dataclass(frozen=True, slots=True)
class RawEvidenceRecord:
    source_record_id: str | None
    event_time: datetime | None
    observed_at: datetime | None
    content_type: str
    payload: Any

    def __post_init__(self) -> None:
        if self.source_record_id is not None:
            require_identifier(self.source_record_id, "source_record_id")
        if self.event_time is not None and (self.event_time.tzinfo is None or self.event_time.utcoffset() is None):
            raise ValueError("event_time must include timezone information")
        if self.observed_at is not None and (self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None):
            raise ValueError("observed_at must include timezone information")
        require_string(self.content_type, "content_type")
        object.__setattr__(self, "payload", freeze_json(self.payload, "payload"))

    @classmethod
    def from_dict(cls, value: object, path: str = "raw_evidence_record") -> "RawEvidenceRecord":
        data = require_mapping(value, path)
        return cls(
            source_record_id=None if data.get("source_record_id") is None else require_identifier(data.get("source_record_id"), f"{path}.source_record_id"),
            event_time=require_optional_datetime(data, "event_time", f"{path}.event_time"),
            observed_at=require_optional_datetime(data, "observed_at", f"{path}.observed_at"),
            content_type=require_string(data.get("content_type"), f"{path}.content_type"),
            payload=data.get("payload"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"content_type": self.content_type, "payload": thaw_json(self.payload)}
        if self.source_record_id is not None:
            result["source_record_id"] = self.source_record_id
        if self.event_time is not None:
            result["event_time"] = datetime_to_wire(self.event_time)
        if self.observed_at is not None:
            result["observed_at"] = datetime_to_wire(self.observed_at)
        return result


@dataclass(frozen=True, slots=True)
class SourceResult:
    query_id: str
    source_type: str
    source_adapter: str
    source_status: str
    truncated: bool
    records: tuple[RawEvidenceRecord, ...]
    warnings: tuple[ProcessingWarning, ...]

    def __post_init__(self) -> None:
        require_identifier(self.query_id, "query_id")
        require_extensible_code(self.source_type, "source_type", _SOURCE_TYPES)
        require_string(self.source_adapter, "source_adapter")
        require_extensible_code(self.source_status, "source_status", _SOURCE_STATUSES)
        require_bool(self.truncated, "truncated")
        if self.source_status == "unavailable" and self.records:
            raise ValueError("unavailable source results must not include records")

    @classmethod
    def from_dict(cls, value: object, path: str = "source_result") -> "SourceResult":
        data = require_mapping(value, path)
        records = require_list(data.get("records", []), f"{path}.records")
        warnings = require_list(data.get("warnings", []), f"{path}.warnings")
        return cls(
            query_id=require_identifier(data.get("query_id"), f"{path}.query_id"),
            source_type=require_extensible_code(data.get("source_type"), f"{path}.source_type", _SOURCE_TYPES),
            source_adapter=require_string(data.get("source_adapter"), f"{path}.source_adapter"),
            source_status=require_extensible_code(data.get("source_status"), f"{path}.source_status", _SOURCE_STATUSES),
            truncated=require_bool(data.get("truncated"), f"{path}.truncated"),
            records=tuple(RawEvidenceRecord.from_dict(record, f"{path}.records[{index}]") for index, record in enumerate(records)),
            warnings=tuple(ProcessingWarning.from_dict(warning) for warning in warnings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "source_type": self.source_type,
            "source_adapter": self.source_adapter,
            "source_status": self.source_status,
            "truncated": self.truncated,
            "records": [record.to_dict() for record in self.records],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class RawEvidenceBatch:
    schema_version: str
    incident_id: str
    plan_id: str
    batch_id: str
    collected_at: datetime
    results: tuple[SourceResult, ...]
    errors: tuple[ProcessingError, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("RawEvidenceBatch requires schema version 1.0")
        for field_name in ("incident_id", "plan_id", "batch_id"):
            require_identifier(getattr(self, field_name), field_name)
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must include timezone information")
        query_ids = [result.query_id for result in self.results]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("results must not repeat query_id values")

    @classmethod
    def from_dict(cls, value: object) -> "RawEvidenceBatch":
        data = require_mapping(value, "raw_evidence_batch")
        require_schema_version(data)
        results = require_list(data.get("results"), "raw_evidence_batch.results")
        errors = require_list(data.get("errors", []), "raw_evidence_batch.errors")
        return cls(
            schema_version=data["schema_version"],
            incident_id=require_identifier(data.get("incident_id"), "raw_evidence_batch.incident_id"),
            plan_id=require_identifier(data.get("plan_id"), "raw_evidence_batch.plan_id"),
            batch_id=require_identifier(data.get("batch_id"), "raw_evidence_batch.batch_id"),
            collected_at=parse_datetime(data.get("collected_at"), "raw_evidence_batch.collected_at"),
            results=tuple(SourceResult.from_dict(result, f"raw_evidence_batch.results[{index}]") for index, result in enumerate(results)),
            errors=tuple(ProcessingError.from_dict(error) for error in errors),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "plan_id": self.plan_id,
            "batch_id": self.batch_id,
            "collected_at": datetime_to_wire(self.collected_at),
            "results": [result.to_dict() for result in self.results],
            "errors": [error.to_dict() for error in self.errors],
        }
