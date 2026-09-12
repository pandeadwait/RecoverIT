"""Safe structured observability port and reference sinks."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from contracts.common import (
    SCHEMA_VERSION,
    freeze_json,
    require_identifier,
    require_string,
    thaw_json,
)


@dataclass(frozen=True, slots=True)
class EvidenceAuditEvent:
    event_type: str
    incident_id: str
    batch_ids: tuple[str, ...]
    evidence_id: str | None = None
    details: Any = None

    def __post_init__(self) -> None:
        require_string(self.event_type, "event_type")
        require_identifier(self.incident_id, "incident_id")
        for batch_id in self.batch_ids:
            require_identifier(batch_id, "batch_ids")
        if self.evidence_id is not None:
            require_identifier(self.evidence_id, "evidence_id")
        object.__setattr__(self, "details", freeze_json(self.details, "details"))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_type": self.event_type,
            "incident_id": self.incident_id,
            "batch_ids": list(self.batch_ids),
            "details": thaw_json(self.details),
        }
        if self.evidence_id is not None:
            result["evidence_id"] = self.evidence_id
        return result


class EvidenceAuditSink(Protocol):
    def emit_all(self, events: tuple[EvidenceAuditEvent, ...]) -> None: ...


class NoOpEvidenceAuditSink(EvidenceAuditSink):
    def emit_all(self, events: tuple[EvidenceAuditEvent, ...]) -> None:
        del events


class InMemoryEvidenceAuditSink(EvidenceAuditSink):
    def __init__(self) -> None:
        self._events: list[EvidenceAuditEvent] = []
        self._lock = RLock()

    @property
    def events(self) -> tuple[EvidenceAuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def emit_all(self, events: tuple[EvidenceAuditEvent, ...]) -> None:
        with self._lock:
            self._events.extend(events)
