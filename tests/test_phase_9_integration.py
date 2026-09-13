"""Final Person 2 pairwise, replay, security, and performance tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.common import thaw_json
from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.incident import IncidentSeed
from contracts.primitives import FixedClock
from evidence.application import (
    EvidenceProcessingResult,
    EvidenceProcessingService,
    InMemoryEvidenceAuditSink,
)
from evidence.context import (
    ContextPublicationService,
    ContextSnapshotBuilder,
    EvidenceContextQueryService,
)
from evidence.normalization import (
    ConfigurationIdentityResolver,
    EvidenceNormalizationService,
)
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator
from timeline.builder import TimelinePersistenceService


FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "phase_9_scenarios.json"
SNAPSHOT_TIME = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class ReplayOutput:
    coordinator: InMemoryRepositoryCoordinator
    incident: IncidentSeed
    batch: RawEvidenceBatch
    processing: EvidenceProcessingResult
    records: tuple[EvidenceRecord, ...]
    snapshot: IncidentContextSnapshot
    elapsed_ms: float


def scenarios() -> tuple[dict[str, Any], ...]:
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("phase 9 replay fixture must be a list")
    return tuple(loaded)


def _permuted(batch: RawEvidenceBatch) -> RawEvidenceBatch:
    return replace(
        batch,
        results=tuple(
            replace(result, records=tuple(reversed(result.records)))
            for result in reversed(batch.results)
        ),
    )


def run_replay(
    scenario: dict[str, Any],
    *,
    permuted: bool = False,
    audit_sink: InMemoryEvidenceAuditSink | None = None,
) -> ReplayOutput:
    started = perf_counter()
    incident = IncidentSeed.from_dict(scenario["incident"])
    batch = RawEvidenceBatch.from_dict(scenario["batch"])
    if permuted:
        batch = _permuted(batch)
    coordinator = InMemoryRepositoryCoordinator()
    processor = EvidenceProcessingService(
        coordinator,
        normalization=EvidenceNormalizationService(
            identity_resolver=ConfigurationIdentityResolver(
                service_aliases={incident.service: incident.service}
            )
        ),
        audit_sink=audit_sink,
    )
    processing = processor.process(incident, (batch,))
    records = coordinator.evidence.query(
        EvidenceFilter(
            schema_version="1.0",
            incident_id=incident.incident_id,
            include_unknown_event_time=True,
            limit=1_000,
        )
    )
    TimelinePersistenceService(coordinator.timelines).build_and_persist(
        incident.incident_id, records, 1
    )
    snapshot = ContextPublicationService(
        coordinator,
        builder=ContextSnapshotBuilder(clock=FixedClock(SNAPSHOT_TIME)),
    ).publish(incident, (batch,), processing.warnings)
    return ReplayOutput(
        coordinator=coordinator,
        incident=incident,
        batch=batch,
        processing=processing,
        records=records,
        snapshot=snapshot,
        elapsed_ms=(perf_counter() - started) * 1_000,
    )


class Phase9ReplayTests(unittest.TestCase):
    def test_person_1_contracts_drive_all_recorded_scenarios_end_to_end(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario["name"]):
                wire_incident = json.loads(json.dumps(scenario["incident"]))
                wire_batch = json.loads(json.dumps(scenario["batch"]))
                IncidentSeed.from_dict(wire_incident)
                RawEvidenceBatch.from_dict(wire_batch)
                output = run_replay(scenario)
                expected = scenario["expected"]
                self.assertEqual(len(output.records), expected["evidence_count"])
                self.assertEqual(
                    len(output.snapshot.warnings), expected["warning_count"]
                )
                self.assertEqual(output.snapshot.revision, expected["revision"])
                self.assertEqual(
                    output.snapshot.snapshot_id, expected["snapshot_id"]
                )

    def test_replay_expectations_cover_order_relationships_and_source_status(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario["name"]):
                output = run_replay(scenario)
                expected = scenario["expected"]
                by_id = {record.evidence_id: record for record in output.records}
                source_order = [
                    by_id[event.evidence_ids[0]].source_type
                    for event in output.snapshot.timeline.events
                ]
                relationships = {
                    item.relationship_type
                    for item in output.snapshot.timeline.relationships
                }
                self.assertEqual(source_order, expected["timeline_source_order"])
                self.assertTrue(
                    set(expected["required_relationships"]).issubset(relationships)
                )
                self.assertTrue(
                    set(expected["forbidden_relationships"]).isdisjoint(relationships)
                )
                for source, state in expected["coverage"].items():
                    self.assertEqual(output.snapshot.source_coverage[source], state)

    def test_person_3_consumes_only_serialized_snapshot_and_read_interface(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario["name"]):
                output = run_replay(scenario)
                wire_snapshot = json.loads(json.dumps(output.snapshot.to_dict()))
                snapshot = IncidentContextSnapshot.from_dict(wire_snapshot)
                reader = EvidenceContextQueryService(
                    output.coordinator.evidence,
                    output.coordinator.contexts,
                )
                self.assertEqual(
                    reader.get_latest_context(output.incident.incident_id), snapshot
                )
                resolved = tuple(
                    reader.get_evidence(item.evidence_id) for item in snapshot.evidence
                )
                self.assertTrue(all(record is not None for record in resolved))

    def test_repeated_and_permuted_replays_are_deterministic_and_idempotent(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario["name"]):
                first = run_replay(scenario)
                permuted = run_replay(scenario, permuted=True)
                self.assertEqual(first.snapshot.to_dict(), permuted.snapshot.to_dict())
                self.assertEqual(
                    tuple(record.to_dict() for record in first.records),
                    tuple(record.to_dict() for record in permuted.records),
                )

                replay = EvidenceProcessingService(
                    first.coordinator,
                    normalization=EvidenceNormalizationService(
                        identity_resolver=ConfigurationIdentityResolver(
                            service_aliases={
                                first.incident.service: first.incident.service
                            }
                        )
                    ),
                ).process(first.incident, (first.batch,))
                self.assertEqual(replay.saved_evidence_ids, ())
                self.assertEqual(
                    set(replay.reused_evidence_ids),
                    {record.evidence_id for record in first.records},
                )
                second_snapshot = ContextPublicationService(
                    first.coordinator,
                    builder=ContextSnapshotBuilder(clock=FixedClock(SNAPSHOT_TIME)),
                ).publish(first.incident, (first.batch,), replay.warnings)
                permuted_second = ContextPublicationService(
                    permuted.coordinator,
                    builder=ContextSnapshotBuilder(clock=FixedClock(SNAPSHOT_TIME)),
                ).publish(
                    permuted.incident,
                    (permuted.batch,),
                    permuted.processing.warnings,
                )
                self.assertEqual(second_snapshot.revision, 2)
                self.assertEqual(
                    second_snapshot.to_dict(), permuted_second.to_dict()
                )

    def test_synthetic_secrets_do_not_cross_person_2_output_boundary(self) -> None:
        for scenario in scenarios():
            forbidden = scenario["expected"].get("forbidden_values", [])
            if not forbidden:
                continue
            with self.subTest(scenario=scenario["name"]):
                audit = InMemoryEvidenceAuditSink()
                output = run_replay(scenario, audit_sink=audit)
                serialized = json.dumps(
                    {
                        "processing": output.processing.to_dict(),
                        "records": [record.to_dict() for record in output.records],
                        "snapshot": output.snapshot.to_dict(),
                        "audit": [event.to_dict() for event in audit.events],
                    },
                    sort_keys=True,
                )
                for value in forbidden:
                    self.assertNotIn(value, serialized)
                self.assertTrue(
                    any(record.quality.redactions_applied for record in output.records)
                )

    def test_measurements_are_external_to_domain_contracts(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario["name"]):
                output = run_replay(scenario)
                raw_count = sum(
                    len(result.records) for result in output.batch.results
                )
                measurements = {
                    "processing_time_ms": output.elapsed_ms,
                    "evidence_volume": len(output.records),
                    "duplicate_ratio": (
                        0.0 if raw_count == 0 else 1 - len(output.records) / raw_count
                    ),
                    "warning_count": len(output.snapshot.warnings),
                    "snapshot_size_bytes": len(
                        json.dumps(output.snapshot.to_dict(), sort_keys=True).encode()
                    ),
                }
                self.assertGreaterEqual(measurements["processing_time_ms"], 0)
                self.assertEqual(
                    measurements["evidence_volume"],
                    scenario["expected"]["evidence_count"],
                )
                self.assertGreater(measurements["snapshot_size_bytes"], 0)
                self.assertNotIn("measurements", output.snapshot.to_dict())

    def test_large_duplicate_group_stays_bounded_and_preserves_provenance(self) -> None:
        scenario = next(
            item for item in scenarios() if item["name"] == "memory_exhaustion"
        )
        batch = RawEvidenceBatch.from_dict(scenario["batch"])
        log_result = next(
            result for result in batch.results if result.source_type == "logs"
        )
        template = log_result.records[0]
        safe_payload = thaw_json(template.payload)
        if not isinstance(safe_payload, dict):
            self.fail("recorded log payload must be an object")
        safe_payload.pop("api_key", None)
        repeated = tuple(
            replace(
                template,
                source_record_id=f"log-oom-{index:04d}",
                payload=safe_payload,
            )
            for index in range(250)
        )
        large_batch = replace(
            batch,
            batch_id="batch-memory-exhaustion-large",
            results=(replace(log_result, records=repeated),),
        )
        large_scenario = {**scenario, "batch": large_batch.to_dict()}
        output = run_replay(large_scenario)
        self.assertLess(output.elapsed_ms, 5_000)
        self.assertEqual(len(output.records), 1)
        aggregate = output.processing.aggregates[0]
        self.assertEqual(aggregate.occurrence_count, 250)
        self.assertEqual(len(aggregate.contributing_provenance), 250)

    def test_person_2_production_code_has_no_reasoning_or_llm_dependency(self) -> None:
        prohibited = (
            "import openai",
            "import anthropic",
            "ReasoningProvider",
            "generate_hypotheses(",
            "rank_hypotheses(",
            "assign_support(",
            "assign_contradiction(",
        )
        files = tuple((PROJECT_ROOT / "evidence").rglob("*.py")) + tuple(
            (PROJECT_ROOT / "timeline").rglob("*.py")
        )
        for path in files:
            if path.name.startswith("._"):
                continue
            source = path.read_text(encoding="utf-8")
            for marker in prohibited:
                self.assertNotIn(marker, source, f"{marker!r} found in {path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
