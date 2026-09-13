"""Timeline and temporal relationship contracts owned by Person 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from contracts.common import SCHEMA_VERSION, datetime_to_wire, parse_datetime, require_extensible_code, require_identifier, require_int, require_list, require_mapping, require_optional_datetime, require_schema_version, require_string

_TIMELINE_CATEGORIES = {"change", "deployment", "symptom", "alert", "action", "verification"}
_RELATIONSHIP_TYPES = {"PRECEDES", "COINCIDES_WITH", "SUPPORTS", "CONTRADICTS", "DEPLOYED_FROM", "AFFECTS", "OBSERVED_ON", "PREDICTS"}
_RELATIONSHIP_CREATORS = {"deterministic", "model", "operator"}
_RELATIONSHIP_STRENGTHS = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    schema_version: str
    timeline_event_id: str
    incident_id: str
    event_time: datetime | None
    time_uncertainty_ms: int | None
    category: str
    title: str
    service: str | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("TimelineEvent requires schema version 1.0")
        require_identifier(self.timeline_event_id, "timeline_event_id")
        require_identifier(self.incident_id, "incident_id")
        if self.event_time is not None and (self.event_time.tzinfo is None or self.event_time.utcoffset() is None):
            raise ValueError("event_time must include timezone information")
        if self.time_uncertainty_ms is not None:
            require_int(self.time_uncertainty_ms, "time_uncertainty_ms", minimum=0)
        require_extensible_code(self.category, "category", _TIMELINE_CATEGORIES)
        require_string(self.title, "title")
        if self.service is not None:
            require_string(self.service, "service")
        if not self.evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        for evidence_id in self.evidence_ids:
            require_identifier(evidence_id, "evidence_ids")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")

    @classmethod
    def from_dict(cls, value: object, path: str = "timeline_event") -> "TimelineEvent":
        data = require_mapping(value, path)
        require_schema_version(data, f"{path}.schema_version")
        evidence_ids = require_list(data.get("evidence_ids"), f"{path}.evidence_ids")
        uncertainty = data.get("time_uncertainty_ms")
        return cls(
            schema_version=data["schema_version"],
            timeline_event_id=require_identifier(data.get("timeline_event_id"), f"{path}.timeline_event_id"),
            incident_id=require_identifier(data.get("incident_id"), f"{path}.incident_id"),
            event_time=require_optional_datetime(data, "event_time", f"{path}.event_time"),
            time_uncertainty_ms=None if uncertainty is None else require_int(uncertainty, f"{path}.time_uncertainty_ms", minimum=0),
            category=require_extensible_code(data.get("category"), f"{path}.category", _TIMELINE_CATEGORIES),
            title=require_string(data.get("title"), f"{path}.title"),
            service=None if data.get("service") is None else require_string(data.get("service"), f"{path}.service"),
            evidence_ids=tuple(require_identifier(item, f"{path}.evidence_ids") for item in evidence_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "timeline_event_id": self.timeline_event_id,
            "incident_id": self.incident_id,
            "category": self.category,
            "title": self.title,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.event_time is not None:
            result["event_time"] = datetime_to_wire(self.event_time)
        if self.time_uncertainty_ms is not None:
            result["time_uncertainty_ms"] = self.time_uncertainty_ms
        if self.service is not None:
            result["service"] = self.service
        return result


@dataclass(frozen=True, slots=True)
class TemporalRelationship:
    schema_version: str
    relationship_id: str
    incident_id: str
    from_event_id: str
    to_event_id: str
    relationship_type: str
    created_by: str
    delta_ms: int | None = None
    strength: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("TemporalRelationship requires schema version 1.0")
        for field_name in ("relationship_id", "incident_id", "from_event_id", "to_event_id"):
            require_identifier(getattr(self, field_name), field_name)
        if self.from_event_id == self.to_event_id:
            raise ValueError("relationship endpoints must differ")
        require_extensible_code(self.relationship_type, "relationship_type", _RELATIONSHIP_TYPES)
        require_extensible_code(self.created_by, "created_by", _RELATIONSHIP_CREATORS)
        if self.delta_ms is not None:
            require_int(self.delta_ms, "delta_ms", minimum=0)
        if self.strength is not None:
            require_extensible_code(self.strength, "strength", _RELATIONSHIP_STRENGTHS)

    @classmethod
    def from_dict(cls, value: object, path: str = "temporal_relationship") -> "TemporalRelationship":
        data = require_mapping(value, path)
        require_schema_version(data, f"{path}.schema_version")
        delta = data.get("delta_ms")
        return cls(
            schema_version=data["schema_version"],
            relationship_id=require_identifier(data.get("relationship_id"), f"{path}.relationship_id"),
            incident_id=require_identifier(data.get("incident_id"), f"{path}.incident_id"),
            from_event_id=require_identifier(data.get("from_event_id"), f"{path}.from_event_id"),
            to_event_id=require_identifier(data.get("to_event_id"), f"{path}.to_event_id"),
            relationship_type=require_extensible_code(data.get("relationship_type"), f"{path}.relationship_type", _RELATIONSHIP_TYPES),
            created_by=require_extensible_code(data.get("created_by"), f"{path}.created_by", _RELATIONSHIP_CREATORS),
            delta_ms=None if delta is None else require_int(delta, f"{path}.delta_ms", minimum=0),
            strength=None if data.get("strength") is None else require_extensible_code(data.get("strength"), f"{path}.strength", _RELATIONSHIP_STRENGTHS),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "relationship_id": self.relationship_id,
            "incident_id": self.incident_id,
            "from_event_id": self.from_event_id,
            "to_event_id": self.to_event_id,
            "relationship_type": self.relationship_type,
            "created_by": self.created_by,
        }
        if self.delta_ms is not None:
            result["delta_ms"] = self.delta_ms
        if self.strength is not None:
            result["strength"] = self.strength
        return result


@dataclass(frozen=True, slots=True)
class Timeline:
    incident_id: str
    events: tuple[TimelineEvent, ...]
    relationships: tuple[TemporalRelationship, ...]

    def __post_init__(self) -> None:
        require_identifier(self.incident_id, "timeline.incident_id")
        event_ids = [event.timeline_event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("timeline event IDs must be unique")
        known_events = set(event_ids)
        for event in self.events:
            if event.incident_id != self.incident_id:
                raise ValueError("all timeline events must belong to the timeline incident")
        relationship_ids = [relationship.relationship_id for relationship in self.relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("timeline relationship IDs must be unique")
        for relationship in self.relationships:
            if relationship.incident_id != self.incident_id:
                raise ValueError("all temporal relationships must belong to the timeline incident")
            if relationship.from_event_id not in known_events or relationship.to_event_id not in known_events:
                raise ValueError("temporal relationships must reference existing timeline events")

    @classmethod
    def from_dict(cls, incident_id: str, events: object, relationships: object) -> "Timeline":
        raw_events = require_list(events, "timeline.events")
        raw_relationships = require_list(relationships, "timeline.relationships")
        return cls(
            incident_id=require_identifier(incident_id, "timeline.incident_id"),
            events=tuple(TimelineEvent.from_dict(event, f"timeline.events[{index}]") for index, event in enumerate(raw_events)),
            relationships=tuple(TemporalRelationship.from_dict(relationship, f"timeline.relationships[{index}]") for index, relationship in enumerate(raw_relationships)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "relationships": [relationship.to_dict() for relationship in self.relationships],
        }
