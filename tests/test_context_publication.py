"""Phase 8 context assembly, publication, and Person 3 boundary tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Protocol
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.context import IncidentContextSnapshot
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.primitives import FixedClock
from contracts.timeline import Timeline
from evidence.context import (
    ContextBuildError,
    ContextPublicationService,
    ContextSnapshotBuilder,
    EvidenceContextQueryService,
    IncidentContextReader,
    SourceCoverageCalculator,
)
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator
from timeline.builder import TimelinePersistenceService


FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT_TIME = datetime(2026, 9, 12, 10, 32, tzinfo=timezone.utc)


def load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def prepared_state() -> tuple[
    InMemoryRepositoryCoordinator,
    RawEvidenceBatch,
    tuple[EvidenceRecord, ...],
    Timeline,
]:
    from tests.test_evidence_processing import batch, incident, service

    coordinator = InMemoryRepositoryCoordinator()
    collected = batch()
    service(coordinator).process(incident(), (collected,))
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
    return coordinator, collected, records, timeline


def builder() -> ContextSnapshotBuilder:
    return ContextSnapshotBuilder(clock=FixedClock(SNAPSHOT_TIME))


def warning(message: str = "A source returned a partial result") -> ProcessingWarning:
    return ProcessingWarning(
        schema_version="1.0",
        code="partial_result",
        message=message,
        path="results[0]",
    )


class Person3Consumer(Protocol):
    def consume(self, reader: IncidentContextReader, incident_id: str) -> int: ...


class ProjectionCountingConsumer:
    def consume(self, reader: IncidentContextReader, incident_id: str) -> int:
        snapshot = reader.get_latest_context(incident_id)
        if snapshot is None:
            return 0
        resolved = tuple(
            reader.get_evidence(projection.evidence_id)
            for projection in snapshot.evidence
        )
        return sum(record is not None for record in resolved)


class CoordinatorPortFacade:
    """Adapter-shaped facade proving the service uses only coordinator ports."""

    def __init__(self, delegate: InMemoryRepositoryCoordinator) -> None:
        self._delegate = delegate
        self.evidence = delegate.evidence
        self.timelines = delegate.timelines
        self.contexts = delegate.contexts

    def save_revision(
        self,
        records: tuple[EvidenceRecord, ...],
        timeline_revision: int,
        timeline: Timeline,
        snapshot: IncidentContextSnapshot,
    ) -> str:
        return self._delegate.save_revision(
            records,
            timeline_revision,
            timeline,
            snapshot,
        )


class ContextPublicationTests(unittest.TestCase):
    def test_first_and_subsequent_publications_are_immutable_revisions(self) -> None:
        from tests.test_evidence_processing import incident

        coordinator, collected, _, _ = prepared_state()
        publisher = ContextPublicationService(coordinator, builder=builder())
        first = publisher.publish(incident(), (collected,))
        second = publisher.publish(incident(), (collected,))

        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(coordinator.contexts.get(first.snapshot_id), first)
        self.assertEqual(coordinator.contexts.get_latest("inc_001"), second)
        self.assertEqual(
            coordinator.timelines.get_revision("inc_001", 2), second.timeline
        )

    def test_coverage_distinguishes_all_four_states(self) -> None:
        _, collected, _, _ = prepared_state()
        by_source = {result.source_type: result for result in collected.results}
        coverage_batch = replace(
            collected,
            results=(
                by_source["logs"],
                replace(by_source["metrics"], records=()),
                replace(
                    by_source["changes"],
                    source_status="unavailable",
                    records=(),
                ),
            ),
        )
        coverage = SourceCoverageCalculator().calculate((coverage_batch,))

        self.assertEqual(coverage["logs"], "available")
        self.assertEqual(coverage["metrics"], "empty")
        self.assertEqual(coverage["changes"], "unavailable")
        self.assertEqual(coverage["deployments"], "not_queried")

    def test_unqueried_sources_preserve_previous_coverage(self) -> None:
        _, collected, _, _ = prepared_state()
        logs_only = replace(collected, results=(collected.results[0],))
        previous = {
            source: "unavailable"
            for source in SourceCoverageCalculator().calculate(())
        }
        coverage = SourceCoverageCalculator().calculate((logs_only,), previous)
        self.assertEqual(coverage["logs"], "available")
        self.assertEqual(coverage["metrics"], "unavailable")

    def test_compact_projections_resolve_to_full_records_without_database_api(self) -> None:
        from tests.test_evidence_processing import incident

        coordinator, collected, records, _ = prepared_state()
        snapshot = ContextPublicationService(
            coordinator, builder=builder()
        ).publish(incident(), (collected,))
        reader: IncidentContextReader = EvidenceContextQueryService(
            coordinator.evidence, coordinator.contexts
        )

        for projection in snapshot.evidence:
            full_record = reader.get_evidence(projection.evidence_id)
            if full_record is None:
                self.fail(f"evidence {projection.evidence_id} did not resolve")
            self.assertEqual(full_record.summary, projection.summary)
        logs = reader.query_evidence(
            EvidenceFilter(
                schema_version="1.0",
                incident_id="inc_001",
                source_types=("logs",),
                include_unknown_event_time=True,
            )
        )
        self.assertEqual(len(logs), 1)
        self.assertEqual(len(snapshot.evidence), len(records))
        consumer: Person3Consumer = ProjectionCountingConsumer()
        self.assertEqual(consumer.consume(reader, "inc_001"), len(records))

    def test_builder_rejects_cross_incident_or_duplicate_timeline_references(self) -> None:
        from tests.test_evidence_processing import incident

        _, _, records, timeline = prepared_state()
        coverage = SourceCoverageCalculator().calculate(())
        with self.assertRaises(ContextBuildError):
            builder().build(
                incident(),
                (replace(records[0], incident_id="inc_other"),) + records[1:],
                timeline,
                coverage,
            )
        duplicate_event = replace(
            timeline.events[0],
            timeline_event_id=f"{timeline.events[0].timeline_event_id}_duplicate",
        )
        duplicate = replace(timeline, events=timeline.events + (duplicate_event,))
        with self.assertRaises(ContextBuildError):
            builder().build(incident(), records, duplicate, coverage)

    def test_warning_merge_deduplicates_and_is_stable_across_revisions(self) -> None:
        from tests.test_evidence_processing import incident

        _, _, records, timeline = prepared_state()
        coverage = SourceCoverageCalculator().calculate(())
        first_warning = warning()
        second_warning = warning("Another partial source")
        first = builder().build(
            incident(), records, timeline, coverage, (first_warning, first_warning)
        )
        second = builder().build(
            incident(),
            records,
            timeline,
            coverage,
            (second_warning, first_warning),
            first,
        )
        self.assertEqual(len(first.warnings), 1)
        self.assertEqual(set(second.warnings), {first_warning, second_warning})
        self.assertEqual(second.revision, 2)

    def test_input_permutations_produce_identical_snapshot_and_fixture(self) -> None:
        from tests.test_evidence_processing import incident

        _, collected, records, timeline = prepared_state()
        coverage = SourceCoverageCalculator().calculate((collected,))
        first = builder().build(incident(), records, timeline, coverage)
        reordered_timeline = replace(
            timeline,
            events=tuple(reversed(timeline.events)),
            relationships=tuple(reversed(timeline.relationships)),
        )
        second = builder().build(
            incident(), tuple(reversed(records)), reordered_timeline, coverage
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.to_dict(), load("context/phase_8_context_snapshot.json"))

    def test_equivalent_repository_adapters_produce_the_same_public_contract(self) -> None:
        from tests.test_evidence_processing import incident

        snapshots = []
        for use_facade in (False, True):
            coordinator, collected, _, _ = prepared_state()
            publication_port = (
                CoordinatorPortFacade(coordinator) if use_facade else coordinator
            )
            snapshots.append(
                ContextPublicationService(publication_port, builder=builder()).publish(
                    incident(), (collected,)
                )
            )
        self.assertEqual(snapshots[0].to_dict(), snapshots[1].to_dict())

    def test_publication_requires_a_persisted_timeline(self) -> None:
        from tests.test_evidence_processing import batch, incident, service

        coordinator = InMemoryRepositoryCoordinator()
        service(coordinator).process(incident(), (batch(),))
        with self.assertRaises(ContextBuildError):
            ContextPublicationService(coordinator, builder=builder()).publish(
                incident(), (batch(),)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
