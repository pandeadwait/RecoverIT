"""Graph integrity validation for deterministic Person 2 timelines."""

from __future__ import annotations

from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from timeline.builder.models import TimelineBuildError


_ALLOWED_RELATIONSHIPS = {"PRECEDES", "COINCIDES_WITH", "DEPLOYED_FROM"}


class TimelineValidator:
    """Ensures timelines reference only the incident's persisted evidence."""

    def validate(
        self,
        incident_id: str,
        records: tuple[EvidenceRecord, ...],
        timeline: Timeline,
    ) -> None:
        if timeline.incident_id != incident_id:
            raise TimelineBuildError("timeline incident does not match requested incident")
        records_by_id = {record.evidence_id: record for record in records}
        if len(records_by_id) != len(records):
            raise TimelineBuildError("evidence IDs must be unique when building a timeline")
        if any(record.incident_id != incident_id for record in records):
            raise TimelineBuildError("all evidence must belong to the requested incident")
        referenced = {
            evidence_id for event in timeline.events for evidence_id in event.evidence_ids
        }
        if referenced != set(records_by_id):
            raise TimelineBuildError(
                "timeline must reference every persisted evidence record exactly once"
            )
        event_evidence_ids = [
            evidence_id for event in timeline.events for evidence_id in event.evidence_ids
        ]
        if len(event_evidence_ids) != len(set(event_evidence_ids)):
            raise TimelineBuildError("evidence must not appear in multiple timeline events")
        if any(event.incident_id != incident_id for event in timeline.events):
            raise TimelineBuildError("timeline event incident mismatch")
        for relationship in timeline.relationships:
            if relationship.relationship_type not in _ALLOWED_RELATIONSHIPS:
                raise TimelineBuildError("unsupported relationship type in deterministic timeline")
            if relationship.created_by != "deterministic":
                raise TimelineBuildError("timeline relationship must be deterministic")
