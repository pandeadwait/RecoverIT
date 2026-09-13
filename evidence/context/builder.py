"""Deterministic construction of Person 2's context snapshot contract."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from contracts.common import SCHEMA_VERSION, canonical_bytes
from contracts.context import (
    EvidenceProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceRecord
from contracts.incident import IncidentSeed
from contracts.primitives import (
    Clock,
    DeterministicIdGenerator,
    IdGenerator,
    SystemClock,
)
from contracts.timeline import Timeline, TimelineEvent


class ContextBuildError(ValueError):
    """Inputs cannot form a complete incident context snapshot."""


class ContextSnapshotBuilder:
    """Builds a compact, reference-valid, immutable context revision."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._ids = id_generator or DeterministicIdGenerator()

    def build(
        self,
        incident: IncidentSeed,
        records: tuple[EvidenceRecord, ...],
        timeline: Timeline,
        source_coverage: Mapping[str, str],
        warnings: tuple[ProcessingWarning, ...] = (),
        previous_snapshot: IncidentContextSnapshot | None = None,
    ) -> IncidentContextSnapshot:
        self._validate_inputs(incident, records, timeline, previous_snapshot)
        revision = 1 if previous_snapshot is None else previous_snapshot.revision + 1
        created_at = self._clock.now()
        if previous_snapshot is not None and created_at < previous_snapshot.created_at:
            raise ContextBuildError("snapshot clock cannot move behind previous revision")
        ordered_records = tuple(sorted(records, key=self._record_sort_key))
        ordered_timeline = self._ordered_timeline(timeline)
        merged_warnings = self._merge_warnings(
            previous_snapshot.warnings if previous_snapshot is not None else (),
            warnings,
        )
        projections = tuple(self._projection(record) for record in ordered_records)
        incident_summary = IncidentSummary(
            service=incident.service,
            environment=incident.environment,
            severity=incident.severity,
            detected_at=incident.detected_at,
            summary=incident.summary,
        )
        identity_material = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": incident.incident_id,
            "revision": revision,
            "created_at": created_at,
            "incident": incident_summary,
            "evidence": projections,
            "timeline": ordered_timeline,
            "source_coverage": dict(source_coverage),
            "warnings": merged_warnings,
        }
        return IncidentContextSnapshot(
            schema_version=SCHEMA_VERSION,
            snapshot_id=self._ids.create("ctx", identity_material),
            incident_id=incident.incident_id,
            revision=revision,
            created_at=created_at,
            incident=incident_summary,
            evidence=projections,
            timeline=ordered_timeline,
            source_coverage=source_coverage,
            warnings=merged_warnings,
        )

    @staticmethod
    def _validate_inputs(
        incident: IncidentSeed,
        records: tuple[EvidenceRecord, ...],
        timeline: Timeline,
        previous_snapshot: IncidentContextSnapshot | None,
    ) -> None:
        if any(record.incident_id != incident.incident_id for record in records):
            raise ContextBuildError("all evidence must belong to the snapshot incident")
        evidence_ids = [record.evidence_id for record in records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ContextBuildError("snapshot evidence IDs must be unique")
        if timeline.incident_id != incident.incident_id:
            raise ContextBuildError("timeline must belong to the snapshot incident")
        referenced = [
            evidence_id for event in timeline.events for evidence_id in event.evidence_ids
        ]
        if len(referenced) != len(set(referenced)):
            raise ContextBuildError(
                "timeline must not reference an evidence record more than once"
            )
        if set(referenced) != set(evidence_ids):
            raise ContextBuildError(
                "timeline must reference every persisted evidence record exactly once"
            )
        if previous_snapshot is not None:
            if previous_snapshot.incident_id != incident.incident_id:
                raise ContextBuildError("previous snapshot belongs to another incident")
            if previous_snapshot.incident.to_dict() != IncidentSummary(
                service=incident.service,
                environment=incident.environment,
                severity=incident.severity,
                detected_at=incident.detected_at,
                summary=incident.summary,
            ).to_dict():
                raise ContextBuildError("incident identity changed across snapshot revisions")

    @staticmethod
    def _record_sort_key(record: EvidenceRecord) -> tuple[int, datetime | None, str]:
        return (
            1 if record.event_time is None else 0,
            record.event_time,
            record.evidence_id,
        )

    @staticmethod
    def _event_sort_key(
        event: TimelineEvent,
    ) -> tuple[int, datetime | None, tuple[str, ...], str]:
        return (
            1 if event.event_time is None else 0,
            event.event_time,
            event.evidence_ids,
            event.timeline_event_id,
        )

    @classmethod
    def _ordered_timeline(cls, timeline: Timeline) -> Timeline:
        return Timeline(
            incident_id=timeline.incident_id,
            events=tuple(sorted(timeline.events, key=cls._event_sort_key)),
            relationships=tuple(
                sorted(timeline.relationships, key=lambda item: item.relationship_id)
            ),
        )

    @staticmethod
    def _projection(record: EvidenceRecord) -> EvidenceProjection:
        return EvidenceProjection(
            evidence_id=record.evidence_id,
            source_type=record.source_type,
            evidence_type=record.evidence_type,
            event_time=record.event_time,
            summary=record.summary,
            quality=record.quality,
        )

    @staticmethod
    def _merge_warnings(
        previous: tuple[ProcessingWarning, ...],
        current: tuple[ProcessingWarning, ...],
    ) -> tuple[ProcessingWarning, ...]:
        unique = {canonical_bytes(item): item for item in previous + current}
        return tuple(unique[key] for key in sorted(unique))
