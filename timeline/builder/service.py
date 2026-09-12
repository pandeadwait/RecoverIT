"""Evidence-to-timeline construction and replaceable revision persistence."""

from __future__ import annotations

from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from timeline.builder.mapper import (
    DefaultEvidenceTimelineMapper,
    EvidenceTimelineMapper,
)
from timeline.builder.models import TimelineBuildError
from timeline.builder.validation import TimelineValidator
from timeline.relationships.service import TemporalRelationshipCalculator
from timeline.repositories.ports import TimelineRepository


class TimelineBuilder:
    """Builds a stable, reference-valid operational history from evidence."""

    def __init__(
        self,
        *,
        mapper: EvidenceTimelineMapper | None = None,
        relationships: TemporalRelationshipCalculator | None = None,
        validator: TimelineValidator | None = None,
    ) -> None:
        self._mapper = mapper or DefaultEvidenceTimelineMapper()
        self._relationships = relationships or TemporalRelationshipCalculator()
        self._validator = validator or TimelineValidator()

    def build(self, incident_id: str, records: tuple[EvidenceRecord, ...]) -> Timeline:
        self._validate_records(incident_id, records)
        ordered_records = tuple(sorted(records, key=self._record_sort_key))
        events = tuple(self._mapper.map(record) for record in ordered_records)
        by_evidence_id = {record.evidence_id: record for record in ordered_records}
        timeline = Timeline(
            incident_id=incident_id,
            events=events,
            relationships=self._relationships.calculate(
                incident_id, events, by_evidence_id
            ),
        )
        self._validator.validate(incident_id, ordered_records, timeline)
        return timeline

    @staticmethod
    def _validate_records(incident_id: str, records: tuple[EvidenceRecord, ...]) -> None:
        if any(record.incident_id != incident_id for record in records):
            raise TimelineBuildError("all evidence must belong to the requested incident")
        evidence_ids = [record.evidence_id for record in records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise TimelineBuildError("evidence IDs must be unique when building a timeline")

    @staticmethod
    def _record_sort_key(record: EvidenceRecord) -> tuple[int, object, str]:
        return (
            1 if record.event_time is None else 0,
            record.event_time,
            record.evidence_id,
        )


class TimelinePersistenceService:
    """Builds and persists a complete deterministic timeline revision."""

    def __init__(
        self,
        repository: TimelineRepository,
        *,
        builder: TimelineBuilder | None = None,
    ) -> None:
        self._repository = repository
        self._builder = builder or TimelineBuilder()

    def build_and_persist(
        self,
        incident_id: str,
        records: tuple[EvidenceRecord, ...],
        revision: int,
    ) -> Timeline:
        timeline = self._builder.build(incident_id, records)
        self._repository.replace_revision(
            incident_id, revision, timeline.events, timeline.relationships
        )
        return timeline
