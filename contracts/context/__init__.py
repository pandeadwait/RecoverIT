"""Incident context snapshot contracts exposed to Person 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from contracts.common import SCHEMA_VERSION, datetime_to_wire, freeze_json, parse_datetime, reject_unknown_fields, require_extensible_code, require_identifier, require_int, require_list, require_mapping, require_schema_version, require_string, thaw_json
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceQuality
from contracts.timeline import Timeline

_SOURCE_TYPES = ("logs", "metrics", "changes", "deployments", "pipelines", "configuration")
_COVERAGE_STATES = {"available", "empty", "not_queried", "unavailable"}


@dataclass(frozen=True, slots=True)
class IncidentSummary:
    service: str
    environment: str
    severity: str
    detected_at: datetime
    summary: str

    def __post_init__(self) -> None:
        for field_name in ("service", "environment", "summary"):
            require_string(getattr(self, field_name), f"incident.{field_name}")
        require_extensible_code(self.severity, "incident.severity", {"info", "warning", "critical"})
        if self.detected_at.tzinfo is None or self.detected_at.utcoffset() is None:
            raise ValueError("incident.detected_at must include timezone information")

    @classmethod
    def from_dict(cls, value: object, path: str = "incident") -> "IncidentSummary":
        data = require_mapping(value, path)
        reject_unknown_fields(data, {"service", "environment", "severity", "detected_at", "summary"}, path)
        return cls(
            service=require_string(data.get("service"), f"{path}.service"),
            environment=require_string(data.get("environment"), f"{path}.environment"),
            severity=require_extensible_code(data.get("severity"), f"{path}.severity", {"info", "warning", "critical"}),
            detected_at=parse_datetime(data.get("detected_at"), f"{path}.detected_at"),
            summary=require_string(data.get("summary"), f"{path}.summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "environment": self.environment,
            "severity": self.severity,
            "detected_at": datetime_to_wire(self.detected_at),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    evidence_id: str
    source_type: str
    evidence_type: str
    event_time: datetime | None
    summary: str
    quality: EvidenceQuality

    def __post_init__(self) -> None:
        require_identifier(self.evidence_id, "evidence.evidence_id")
        require_extensible_code(self.source_type, "evidence.source_type", set(_SOURCE_TYPES))
        require_string(self.evidence_type, "evidence.evidence_type")
        if self.event_time is not None and (self.event_time.tzinfo is None or self.event_time.utcoffset() is None):
            raise ValueError("evidence.event_time must include timezone information")
        require_string(self.summary, "evidence.summary")

    @classmethod
    def from_dict(cls, value: object, path: str = "evidence_projection") -> "EvidenceProjection":
        data = require_mapping(value, path)
        reject_unknown_fields(data, {"evidence_id", "source_type", "evidence_type", "event_time", "summary", "quality"}, path)
        event_value = data.get("event_time")
        return cls(
            evidence_id=require_identifier(data.get("evidence_id"), f"{path}.evidence_id"),
            source_type=require_extensible_code(data.get("source_type"), f"{path}.source_type", set(_SOURCE_TYPES)),
            evidence_type=require_string(data.get("evidence_type"), f"{path}.evidence_type"),
            event_time=None if event_value is None else parse_datetime(event_value, f"{path}.event_time"),
            summary=require_string(data.get("summary"), f"{path}.summary"),
            quality=EvidenceQuality.from_dict(data.get("quality"), f"{path}.quality"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "evidence_type": self.evidence_type,
            "summary": self.summary,
            "quality": self.quality.to_dict(),
        }
        if self.event_time is not None:
            result["event_time"] = datetime_to_wire(self.event_time)
        return result


@dataclass(frozen=True, slots=True)
class IncidentContextSnapshot:
    schema_version: str
    snapshot_id: str
    incident_id: str
    revision: int
    created_at: datetime
    incident: IncidentSummary
    evidence: tuple[EvidenceProjection, ...]
    timeline: Timeline
    source_coverage: Mapping[str, str]
    warnings: tuple[ProcessingWarning, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("IncidentContextSnapshot requires schema version 1.0")
        require_identifier(self.snapshot_id, "snapshot_id")
        require_identifier(self.incident_id, "incident_id")
        require_int(self.revision, "revision", minimum=1)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        if self.timeline.incident_id != self.incident_id:
            raise ValueError("snapshot timeline must belong to the snapshot incident")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("snapshot evidence IDs must be unique")
        known_evidence = set(evidence_ids)
        for event in self.timeline.events:
            if not set(event.evidence_ids).issubset(known_evidence):
                raise ValueError("timeline events must reference evidence in the snapshot")
        coverage = require_mapping(self.source_coverage, "source_coverage")
        missing_sources = set(_SOURCE_TYPES) - set(coverage)
        if missing_sources:
            raise ValueError(f"source_coverage is missing required sources: {sorted(missing_sources)}")
        normalized_coverage: dict[str, str] = {}
        for source, state in coverage.items():
            require_extensible_code(source, "source_coverage.source", set(_SOURCE_TYPES))
            normalized_coverage[source] = require_extensible_code(state, f"source_coverage.{source}", _COVERAGE_STATES)
        object.__setattr__(self, "source_coverage", freeze_json(normalized_coverage, "source_coverage"))

    @classmethod
    def from_dict(cls, value: object) -> "IncidentContextSnapshot":
        data = require_mapping(value, "incident_context_snapshot")
        reject_unknown_fields(
            data,
            {"schema_version", "snapshot_id", "incident_id", "revision", "created_at", "incident", "evidence", "timeline", "relationships", "source_coverage", "warnings"},
            "incident_context_snapshot",
        )
        require_schema_version(data)
        evidence = require_list(data.get("evidence"), "incident_context_snapshot.evidence")
        timeline_events = require_list(data.get("timeline"), "incident_context_snapshot.timeline")
        relationships = require_list(data.get("relationships", []), "incident_context_snapshot.relationships")
        warnings = require_list(data.get("warnings", []), "incident_context_snapshot.warnings")
        return cls(
            schema_version=data["schema_version"],
            snapshot_id=require_identifier(data.get("snapshot_id"), "incident_context_snapshot.snapshot_id"),
            incident_id=require_identifier(data.get("incident_id"), "incident_context_snapshot.incident_id"),
            revision=require_int(data.get("revision"), "incident_context_snapshot.revision", minimum=1),
            created_at=parse_datetime(data.get("created_at"), "incident_context_snapshot.created_at"),
            incident=IncidentSummary.from_dict(data.get("incident"), "incident_context_snapshot.incident"),
            evidence=tuple(EvidenceProjection.from_dict(item, f"incident_context_snapshot.evidence[{index}]") for index, item in enumerate(evidence)),
            timeline=Timeline.from_dict(data.get("incident_id"), timeline_events, relationships),
            source_coverage=require_mapping(data.get("source_coverage"), "incident_context_snapshot.source_coverage"),
            warnings=tuple(ProcessingWarning.from_dict(warning) for warning in warnings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "incident_id": self.incident_id,
            "revision": self.revision,
            "created_at": datetime_to_wire(self.created_at),
            "incident": self.incident.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "timeline": [event.to_dict() for event in self.timeline.events],
            "relationships": [relationship.to_dict() for relationship in self.timeline.relationships],
            "source_coverage": thaw_json(self.source_coverage),
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
