"""Deterministic mapping from immutable evidence to timeline events."""

from __future__ import annotations

from typing import Mapping, Protocol

from contracts.common import SCHEMA_VERSION, thaw_json
from contracts.evidence import EvidenceRecord
from contracts.primitives import DeterministicIdGenerator, IdGenerator
from contracts.timeline import TimelineEvent


class EvidenceTimelineMapper(Protocol):
    def map(self, record: EvidenceRecord) -> TimelineEvent: ...


class DefaultEvidenceTimelineMapper(EvidenceTimelineMapper):
    """Maps current evidence types while retaining extensible category support."""

    _SOURCE_CATEGORIES = {
        "changes": "change",
        "configuration": "change",
        "deployments": "deployment",
        "pipelines": "change",
        "logs": "symptom",
        "metrics": "symptom",
    }
    _EVIDENCE_CATEGORIES = {
        "alert": "alert",
        "alert_event": "alert",
        "action": "action",
        "action_event": "action",
        "verification": "verification",
        "verification_event": "verification",
    }

    def __init__(self, id_generator: IdGenerator | None = None) -> None:
        self._ids = id_generator or DeterministicIdGenerator()

    def map(self, record: EvidenceRecord) -> TimelineEvent:
        category = self._EVIDENCE_CATEGORIES.get(
            record.evidence_type,
            self._SOURCE_CATEGORIES.get(record.source_type, "symptom"),
        )
        return TimelineEvent(
            schema_version=SCHEMA_VERSION,
            timeline_event_id=self._ids.create(
                "tle",
                {
                    "incident_id": record.incident_id,
                    "evidence_id": record.evidence_id,
                    "category": category,
                },
            ),
            incident_id=record.incident_id,
            event_time=record.event_time,
            time_uncertainty_ms=self._time_uncertainty(record),
            category=category,
            title=record.summary,
            service=record.service,
            evidence_ids=(record.evidence_id,),
        )

    @staticmethod
    def _time_uncertainty(record: EvidenceRecord) -> int | None:
        attributes = thaw_json(record.attributes)
        if not isinstance(attributes, Mapping):
            return None
        metadata = attributes.get("x-processing-metadata")
        if not isinstance(metadata, Mapping):
            return None
        value = metadata.get("time_uncertainty_ms")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value
