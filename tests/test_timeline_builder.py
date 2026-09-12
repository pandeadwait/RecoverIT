"""Phase 7 deterministic timeline construction and persistence tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import unittest

from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.timeline import Timeline, TimelineEvent
from evidence.application import EvidenceProcessingService
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator
from tests.repository_contract import evidence
from timeline.builder import (
    DefaultEvidenceTimelineMapper,
    TimelineBuildError,
    TimelineBuilder,
    TimelinePersistenceService,
    TimelineRuleConfiguration,
    TimelineValidator,
)
from timeline.relationships import TemporalRelationshipCalculator


FIXTURES = Path(__file__).parent / "fixtures"


def load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def persisted_records() -> tuple[EvidenceRecord, ...]:
    from tests.test_evidence_processing import batch, incident, service

    coordinator = InMemoryRepositoryCoordinator()
    service(coordinator).process(incident(), (batch(),))
    return coordinator.evidence.query(
        EvidenceFilter(
            schema_version="1.0",
            incident_id="inc_001",
            include_unknown_event_time=True,
            limit=100,
        )
    )


class TimelineBuilderTests(unittest.TestCase):
    def test_maps_persisted_evidence_to_complete_chronological_timeline(self) -> None:
        records = persisted_records()
        timeline = TimelineBuilder().build("inc_001", records)
        categories = {
            event.evidence_ids[0]: event.category for event in timeline.events
        }
        source_by_id = {record.evidence_id: record.source_type for record in records}
        expected_categories = {
            "changes": "change",
            "configuration": "change",
            "deployments": "deployment",
            "pipelines": "change",
            "logs": "symptom",
            "metrics": "symptom",
        }
        self.assertEqual(
            {
                source_by_id[evidence_id]: category
                for evidence_id, category in categories.items()
            },
            expected_categories,
        )
        self.assertEqual(
            timeline.events[-1].evidence_ids[0],
            next(
                record.evidence_id
                for record in records
                if record.source_type == "configuration"
            ),
        )
        self.assertEqual(
            {evidence_id for event in timeline.events for evidence_id in event.evidence_ids},
            {record.evidence_id for record in records},
        )
        self.assertTrue(
            all(
                relationship.relationship_type
                in {"PRECEDES", "COINCIDES_WITH", "DEPLOYED_FROM"}
                for relationship in timeline.relationships
            )
        )
        self.assertNotIn(
            "CAUSES",
            {relationship.relationship_type for relationship in timeline.relationships},
        )

    def test_equal_timestamps_use_evidence_id_and_unknown_times_are_last(self) -> None:
        first = evidence("ev_z")
        second = evidence("ev_a")
        unknown = replace(evidence("ev_unknown"), event_time=None)
        timeline = TimelineBuilder().build("inc_001", (first, unknown, second))
        self.assertEqual(
            tuple(event.evidence_ids[0] for event in timeline.events),
            ("ev_a", "ev_z", "ev_unknown"),
        )

    def test_uncertainty_and_window_define_coincidence_boundary(self) -> None:
        first = evidence("ev_first")
        base_time = first.event_time
        self.assertIsNotNone(base_time)
        second_at_boundary = replace(
            evidence("ev_second"), event_time=base_time + timedelta(milliseconds=100)
        )
        calculator = TemporalRelationshipCalculator(
            configuration=TimelineRuleConfiguration(coincidence_window_ms=100)
        )
        boundary_timeline = TimelineBuilder(relationships=calculator).build(
            "inc_001", (first, second_at_boundary)
        )
        self.assertEqual(
            boundary_timeline.relationships[0].relationship_type, "COINCIDES_WITH"
        )
        self.assertEqual(boundary_timeline.relationships[0].delta_ms, 100)

        second_after_boundary = replace(
            second_at_boundary, event_time=base_time + timedelta(milliseconds=101)
        )
        after_timeline = TimelineBuilder(relationships=calculator).build(
            "inc_001", (first, second_after_boundary)
        )
        self.assertEqual(after_timeline.relationships[0].relationship_type, "PRECEDES")

    def test_time_uncertainty_can_make_nearby_events_coincide(self) -> None:
        first = replace(
            evidence("ev_first"),
            attributes={"x-processing-metadata": {"time_uncertainty_ms": 150}},
        )
        second = replace(
            evidence("ev_second"),
            event_time=first.event_time + timedelta(milliseconds=150),
        )
        timeline = TimelineBuilder(
            relationships=TemporalRelationshipCalculator(
                configuration=TimelineRuleConfiguration(coincidence_window_ms=0)
            )
        ).build("inc_001", (first, second))
        self.assertEqual(timeline.events[0].time_uncertainty_ms, 150)
        self.assertEqual(timeline.relationships[0].relationship_type, "COINCIDES_WITH")

    def test_deployed_from_requires_shared_explicit_revision(self) -> None:
        change = replace(
            evidence("ev_change"),
            source_type="changes",
            evidence_type="source_change",
            summary="Change abc123",
            attributes={"revision": "abc123"},
            event_time=evidence().event_time - timedelta(minutes=10),
        )
        deployment = replace(
            evidence("ev_deployment"),
            source_type="deployments",
            evidence_type="deployment_event",
            summary="Deployment deploy-1 succeeded",
            attributes={"deployment_id": "deploy-1", "revision": "abc123"},
        )
        linked = TimelineBuilder().build("inc_001", (deployment, change))
        deployed_from = [
            relationship
            for relationship in linked.relationships
            if relationship.relationship_type == "DEPLOYED_FROM"
        ]
        self.assertEqual(len(deployed_from), 1)
        self.assertEqual(
            deployed_from[0].from_event_id,
            next(
                event.timeline_event_id
                for event in linked.events
                if event.evidence_ids == ("ev_deployment",)
            ),
        )

        unrelated = replace(
            deployment,
            attributes={"deployment_id": "deploy-1", "revision": "def456"},
        )
        unlinked = TimelineBuilder().build("inc_001", (unrelated, change))
        self.assertFalse(
            any(
                item.relationship_type == "DEPLOYED_FROM"
                for item in unlinked.relationships
            )
        )

    def test_extended_alert_action_and_verification_categories_are_preserved(self) -> None:
        mapper = DefaultEvidenceTimelineMapper()
        for evidence_type, category in (
            ("alert", "alert"),
            ("action", "action"),
            ("verification", "verification"),
        ):
            with self.subTest(evidence_type=evidence_type):
                record = replace(
                    evidence(f"ev_{evidence_type}"), evidence_type=evidence_type
                )
                self.assertEqual(mapper.map(record).category, category)

    def test_input_permutation_keeps_timeline_bytes_and_relationship_ids_stable(self) -> None:
        records = persisted_records()
        builder = TimelineBuilder()
        first = builder.build("inc_001", records)
        second = builder.build("inc_001", tuple(reversed(records)))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            first.to_dict(), load("timeline/phase_7_built_timeline.json")
        )

    def test_validator_rejects_non_persisted_evidence_and_cross_incident_records(self) -> None:
        record = evidence("ev_known")
        invalid_event = TimelineEvent(
            schema_version="1.0",
            timeline_event_id="tle_invalid",
            incident_id="inc_001",
            event_time=record.event_time,
            time_uncertainty_ms=None,
            category="symptom",
            title="Invalid reference",
            service=record.service,
            evidence_ids=("ev_missing",),
        )
        invalid = Timeline(incident_id="inc_001", events=(invalid_event,), relationships=())
        with self.assertRaises(TimelineBuildError):
            TimelineValidator().validate("inc_001", (record,), invalid)
        with self.assertRaises(TimelineBuildError):
            TimelineBuilder().build("inc_001", (replace(record, incident_id="inc_other"),))

    def test_persists_complete_revision_through_timeline_port(self) -> None:
        from tests.test_evidence_processing import batch, incident, service

        coordinator = InMemoryRepositoryCoordinator()
        service(coordinator).process(incident(), (batch(),))
        records = coordinator.evidence.query(
            EvidenceFilter(
                schema_version="1.0",
                incident_id="inc_001",
                include_unknown_event_time=True,
                limit=100,
            )
        )
        timeline = TimelinePersistenceService(coordinator.timelines).build_and_persist(
            "inc_001", records, 1
        )
        self.assertEqual(coordinator.timelines.get_revision("inc_001", 1), timeline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
