"""Deterministic, non-causal temporal relationship calculation."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from contracts.common import SCHEMA_VERSION, thaw_json
from contracts.evidence import EvidenceRecord
from contracts.primitives import DeterministicIdGenerator, IdGenerator
from contracts.timeline import TemporalRelationship, TimelineEvent
from timeline.builder.models import TimelineRuleConfiguration


class TemporalRelationshipCalculator:
    """Emits only configured temporal and explicit revision-link relationships."""

    def __init__(
        self,
        *,
        configuration: TimelineRuleConfiguration | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._configuration = configuration or TimelineRuleConfiguration()
        self._ids = id_generator or DeterministicIdGenerator()

    def calculate(
        self,
        incident_id: str,
        events: tuple[TimelineEvent, ...],
        records_by_evidence_id: Mapping[str, EvidenceRecord],
    ) -> tuple[TemporalRelationship, ...]:
        relationships = self._adjacent_temporal_relationships(incident_id, events)
        relationships.extend(
            self._deployment_revision_relationships(
                incident_id, events, records_by_evidence_id
            )
        )
        return tuple(sorted(relationships, key=lambda item: item.relationship_id))

    def _adjacent_temporal_relationships(
        self, incident_id: str, events: tuple[TimelineEvent, ...]
    ) -> list[TemporalRelationship]:
        known = [event for event in events if event.event_time is not None]
        relationships: list[TemporalRelationship] = []
        for earlier, later in zip(known, known[1:]):
            delta_ms = int((later.event_time - earlier.event_time).total_seconds() * 1_000)
            relationship_type = (
                "COINCIDES_WITH"
                if self._coincides(earlier, later)
                else "PRECEDES"
            )
            relationships.append(
                self._relationship(
                    incident_id,
                    earlier.timeline_event_id,
                    later.timeline_event_id,
                    relationship_type,
                    delta_ms,
                )
            )
        return relationships

    def _coincides(self, earlier: TimelineEvent, later: TimelineEvent) -> bool:
        if earlier.event_time is None or later.event_time is None:
            return False
        earlier_uncertainty = earlier.time_uncertainty_ms or 0
        later_uncertainty = later.time_uncertainty_ms or 0
        earlier_end = earlier.event_time + timedelta(milliseconds=earlier_uncertainty)
        later_start = later.event_time - timedelta(milliseconds=later_uncertainty)
        gap_ms = int((later_start - earlier_end).total_seconds() * 1_000)
        return gap_ms <= self._configuration.coincidence_window_ms

    def _deployment_revision_relationships(
        self,
        incident_id: str,
        events: tuple[TimelineEvent, ...],
        records_by_evidence_id: Mapping[str, EvidenceRecord],
    ) -> list[TemporalRelationship]:
        event_by_evidence_id = {
            event.evidence_ids[0]: event
            for event in events
            if len(event.evidence_ids) == 1
        }
        changes = [
            record
            for record in records_by_evidence_id.values()
            if record.source_type == "changes"
        ]
        deployments = [
            record
            for record in records_by_evidence_id.values()
            if record.source_type == "deployments"
        ]
        relationships: list[TemporalRelationship] = []
        for deployment in sorted(deployments, key=lambda item: item.evidence_id):
            deployment_event = event_by_evidence_id.get(deployment.evidence_id)
            deployment_revision = self._revision(deployment)
            if deployment_event is None or deployment_revision is None:
                continue
            for change in sorted(changes, key=lambda item: item.evidence_id):
                change_event = event_by_evidence_id.get(change.evidence_id)
                if change_event is None or self._revision(change) != deployment_revision:
                    continue
                delta_ms = self._absolute_delta_ms(deployment_event, change_event)
                relationships.append(
                    self._relationship(
                        incident_id,
                        deployment_event.timeline_event_id,
                        change_event.timeline_event_id,
                        "DEPLOYED_FROM",
                        delta_ms,
                    )
                )
        return relationships

    @staticmethod
    def _revision(record: EvidenceRecord) -> str | None:
        attributes = thaw_json(record.attributes)
        if not isinstance(attributes, Mapping):
            return None
        for name in ("revision", "commit_sha"):
            value = attributes.get(name)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _absolute_delta_ms(
        first: TimelineEvent, second: TimelineEvent
    ) -> int | None:
        if first.event_time is None or second.event_time is None:
            return None
        return abs(int((first.event_time - second.event_time).total_seconds() * 1_000))

    def _relationship(
        self,
        incident_id: str,
        from_event_id: str,
        to_event_id: str,
        relationship_type: str,
        delta_ms: int | None,
    ) -> TemporalRelationship:
        return TemporalRelationship(
            schema_version=SCHEMA_VERSION,
            relationship_id=self._ids.create(
                "rel",
                {
                    "incident_id": incident_id,
                    "from_event_id": from_event_id,
                    "to_event_id": to_event_id,
                    "relationship_type": relationship_type,
                },
            ),
            incident_id=incident_id,
            from_event_id=from_event_id,
            to_event_id=to_event_id,
            relationship_type=relationship_type,
            created_by="deterministic",
            delta_ms=delta_ms,
        )
