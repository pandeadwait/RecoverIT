"""Incident intake contract consumed by Person 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from contracts.common import SCHEMA_VERSION, datetime_to_wire, freeze_json, parse_datetime, require_extensible_code, require_identifier, require_mapping, require_schema_version, require_string, thaw_json


@dataclass(frozen=True, slots=True)
class IncidentSeed:
    schema_version: str
    incident_id: str
    external_alert_id: str
    service: str
    environment: str
    severity: str
    detected_at: datetime
    received_at: datetime
    summary: str
    labels: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("IncidentSeed requires schema version 1.0")
        for field_name in ("incident_id", "external_alert_id"):
            require_identifier(getattr(self, field_name), field_name)
        for field_name in ("service", "environment", "summary"):
            require_string(getattr(self, field_name), field_name)
        require_extensible_code(self.severity, "severity", {"info", "warning", "critical"})
        for field_name in ("detected_at", "received_at"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"{field_name} must include timezone information")
        labels = require_mapping(self.labels, "labels")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in labels.items()):
            raise ValueError("labels must map strings to strings")
        object.__setattr__(self, "labels", freeze_json(dict(labels), "labels"))

    @classmethod
    def from_dict(cls, value: object) -> "IncidentSeed":
        data = require_mapping(value, "incident_seed")
        require_schema_version(data)
        labels = require_mapping(data.get("labels", {}), "incident_seed.labels")
        return cls(
            schema_version=data["schema_version"],
            incident_id=require_identifier(data.get("incident_id"), "incident_seed.incident_id"),
            external_alert_id=require_identifier(data.get("external_alert_id"), "incident_seed.external_alert_id"),
            service=require_string(data.get("service"), "incident_seed.service"),
            environment=require_string(data.get("environment"), "incident_seed.environment"),
            severity=require_extensible_code(data.get("severity"), "incident_seed.severity", {"info", "warning", "critical"}),
            detected_at=parse_datetime(data.get("detected_at"), "incident_seed.detected_at"),
            received_at=parse_datetime(data.get("received_at"), "incident_seed.received_at"),
            summary=require_string(data.get("summary"), "incident_seed.summary"),
            labels={
                require_string(key, "incident_seed.labels.key"): require_string(item, f"incident_seed.labels.{key}")
                for key, item in labels.items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "external_alert_id": self.external_alert_id,
            "service": self.service,
            "environment": self.environment,
            "severity": self.severity,
            "detected_at": datetime_to_wire(self.detected_at),
            "received_at": datetime_to_wire(self.received_at),
            "summary": self.summary,
            "labels": thaw_json(self.labels),
        }
