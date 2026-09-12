"""Canonical evidence contracts owned by Person 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from contracts.common import SCHEMA_VERSION, datetime_to_wire, freeze_json, parse_datetime, require_bool, require_extensible_code, require_identifier, require_int, require_list, require_mapping, require_optional_datetime, require_schema_version, require_string, thaw_json

_SOURCE_TYPES = {"logs", "metrics", "changes", "deployments", "pipelines", "configuration"}
_RELIABILITIES = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    batch_id: str
    query_id: str
    source_record_id: str | None
    source_adapter: str
    raw_payload_hash: str

    def __post_init__(self) -> None:
        require_identifier(self.batch_id, "provenance.batch_id")
        require_identifier(self.query_id, "provenance.query_id")
        if self.source_record_id is not None:
            require_identifier(self.source_record_id, "provenance.source_record_id")
        require_string(self.source_adapter, "provenance.source_adapter")
        digest = require_string(self.raw_payload_hash, "provenance.raw_payload_hash")
        if ":" not in digest:
            raise ValueError("raw_payload_hash must include its algorithm prefix")

    @classmethod
    def from_dict(cls, value: object, path: str = "evidence_provenance") -> "EvidenceProvenance":
        data = require_mapping(value, path)
        return cls(
            batch_id=require_identifier(data.get("batch_id"), f"{path}.batch_id"),
            query_id=require_identifier(data.get("query_id"), f"{path}.query_id"),
            source_record_id=None if data.get("source_record_id") is None else require_identifier(data.get("source_record_id"), f"{path}.source_record_id"),
            source_adapter=require_string(data.get("source_adapter"), f"{path}.source_adapter"),
            raw_payload_hash=require_string(data.get("raw_payload_hash"), f"{path}.raw_payload_hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "batch_id": self.batch_id,
            "query_id": self.query_id,
            "source_adapter": self.source_adapter,
            "raw_payload_hash": self.raw_payload_hash,
        }
        if self.source_record_id is not None:
            result["source_record_id"] = self.source_record_id
        return result


@dataclass(frozen=True, slots=True)
class EvidenceQuality:
    reliability: str
    freshness_seconds: int | None
    truncated_source: bool
    redactions_applied: bool

    def __post_init__(self) -> None:
        require_extensible_code(self.reliability, "quality.reliability", _RELIABILITIES)
        if self.freshness_seconds is not None:
            require_int(self.freshness_seconds, "quality.freshness_seconds", minimum=0)
        require_bool(self.truncated_source, "quality.truncated_source")
        require_bool(self.redactions_applied, "quality.redactions_applied")

    @classmethod
    def from_dict(cls, value: object, path: str = "evidence_quality") -> "EvidenceQuality":
        data = require_mapping(value, path)
        freshness = data.get("freshness_seconds")
        return cls(
            reliability=require_extensible_code(data.get("reliability"), f"{path}.reliability", _RELIABILITIES),
            freshness_seconds=None if freshness is None else require_int(freshness, f"{path}.freshness_seconds", minimum=0),
            truncated_source=require_bool(data.get("truncated_source"), f"{path}.truncated_source"),
            redactions_applied=require_bool(data.get("redactions_applied"), f"{path}.redactions_applied"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "reliability": self.reliability,
            "truncated_source": self.truncated_source,
            "redactions_applied": self.redactions_applied,
        }
        if self.freshness_seconds is not None:
            result["freshness_seconds"] = self.freshness_seconds
        return result


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    schema_version: str
    evidence_id: str
    incident_id: str
    source_type: str
    evidence_type: str
    service: str | None
    event_time: datetime | None
    observed_at: datetime | None
    collected_at: datetime
    summary: str
    attributes: Any
    provenance: EvidenceProvenance
    quality: EvidenceQuality

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("EvidenceRecord requires schema version 1.0")
        require_identifier(self.evidence_id, "evidence_id")
        require_identifier(self.incident_id, "incident_id")
        require_extensible_code(self.source_type, "source_type", _SOURCE_TYPES)
        require_string(self.evidence_type, "evidence_type")
        if self.service is not None:
            require_string(self.service, "service")
        for field_name in ("event_time", "observed_at", "collected_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
                raise ValueError(f"{field_name} must include timezone information")
        require_string(self.summary, "summary")
        object.__setattr__(self, "attributes", freeze_json(self.attributes, "attributes"))

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceRecord":
        data = require_mapping(value, "evidence_record")
        require_schema_version(data)
        return cls(
            schema_version=data["schema_version"],
            evidence_id=require_identifier(data.get("evidence_id"), "evidence_record.evidence_id"),
            incident_id=require_identifier(data.get("incident_id"), "evidence_record.incident_id"),
            source_type=require_extensible_code(data.get("source_type"), "evidence_record.source_type", _SOURCE_TYPES),
            evidence_type=require_string(data.get("evidence_type"), "evidence_record.evidence_type"),
            service=None if data.get("service") is None else require_string(data.get("service"), "evidence_record.service"),
            event_time=require_optional_datetime(data, "event_time", "evidence_record.event_time"),
            observed_at=require_optional_datetime(data, "observed_at", "evidence_record.observed_at"),
            collected_at=parse_datetime(data.get("collected_at"), "evidence_record.collected_at"),
            summary=require_string(data.get("summary"), "evidence_record.summary"),
            attributes=data.get("attributes", {}),
            provenance=EvidenceProvenance.from_dict(data.get("provenance"), "evidence_record.provenance"),
            quality=EvidenceQuality.from_dict(data.get("quality"), "evidence_record.quality"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "incident_id": self.incident_id,
            "source_type": self.source_type,
            "evidence_type": self.evidence_type,
            "collected_at": datetime_to_wire(self.collected_at),
            "summary": self.summary,
            "attributes": thaw_json(self.attributes),
            "provenance": self.provenance.to_dict(),
            "quality": self.quality.to_dict(),
        }
        if self.service is not None:
            result["service"] = self.service
        if self.event_time is not None:
            result["event_time"] = datetime_to_wire(self.event_time)
        if self.observed_at is not None:
            result["observed_at"] = datetime_to_wire(self.observed_at)
        return result


@dataclass(frozen=True, slots=True)
class EvidenceFilter:
    schema_version: str
    incident_id: str
    evidence_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    evidence_types: tuple[str, ...] = ()
    service: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    include_unknown_event_time: bool = False
    limit: int = 100

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("EvidenceFilter requires schema version 1.0")
        require_identifier(self.incident_id, "filter.incident_id")
        for identifier in self.evidence_ids:
            require_identifier(identifier, "filter.evidence_ids")
        for source_type in self.source_types:
            require_extensible_code(source_type, "filter.source_types", _SOURCE_TYPES)
        for evidence_type in self.evidence_types:
            require_string(evidence_type, "filter.evidence_types")
        if self.service is not None:
            require_string(self.service, "filter.service")
        for field_name in ("start_time", "end_time"):
            timestamp = getattr(self, field_name)
            if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
                raise ValueError(f"{field_name} must include timezone information")
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
        require_bool(self.include_unknown_event_time, "filter.include_unknown_event_time")
        require_int(self.limit, "filter.limit", minimum=1)

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceFilter":
        data = require_mapping(value, "evidence_filter")
        evidence_ids = require_list(data.get("evidence_ids", []), "evidence_filter.evidence_ids")
        source_types = require_list(data.get("source_types", []), "evidence_filter.source_types")
        evidence_types = require_list(data.get("evidence_types", []), "evidence_filter.evidence_types")
        return cls(
            schema_version=require_schema_version(data, "evidence_filter.schema_version"),
            incident_id=require_identifier(data.get("incident_id"), "evidence_filter.incident_id"),
            evidence_ids=tuple(require_identifier(item, "evidence_filter.evidence_ids") for item in evidence_ids),
            source_types=tuple(require_extensible_code(item, "evidence_filter.source_types", _SOURCE_TYPES) for item in source_types),
            evidence_types=tuple(require_string(item, "evidence_filter.evidence_types") for item in evidence_types),
            service=None if data.get("service") is None else require_string(data.get("service"), "evidence_filter.service"),
            start_time=require_optional_datetime(data, "start_time", "evidence_filter.start_time"),
            end_time=require_optional_datetime(data, "end_time", "evidence_filter.end_time"),
            include_unknown_event_time=require_bool(data.get("include_unknown_event_time", False), "evidence_filter.include_unknown_event_time"),
            limit=require_int(data.get("limit", 100), "evidence_filter.limit", minimum=1),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "evidence_ids": list(self.evidence_ids),
            "source_types": list(self.source_types),
            "evidence_types": list(self.evidence_types),
            "include_unknown_event_time": self.include_unknown_event_time,
            "limit": self.limit,
        }
        if self.service is not None:
            result["service"] = self.service
        if self.start_time is not None:
            result["start_time"] = datetime_to_wire(self.start_time)
        if self.end_time is not None:
            result["end_time"] = datetime_to_wire(self.end_time)
        return result
