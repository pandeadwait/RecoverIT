"""Phase 6 evidence-processing integration, replay, and failure-path tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.evidence import EvidenceFilter
from contracts.incident import IncidentSeed
from evidence.application import (
    EvidenceProcessingError,
    EvidenceProcessingService,
    InMemoryEvidenceAuditSink,
)
from evidence.normalization import ConfigurationIdentityResolver, EvidenceNormalizationService
from evidence.repositories.ports import RepositoryError
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator


FIXTURES = Path(__file__).parent / "fixtures"


def load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def incident() -> IncidentSeed:
    return IncidentSeed.from_dict(load("contracts/incident_seed.json"))


def batch() -> RawEvidenceBatch:
    return RawEvidenceBatch.from_dict(load("normalization/all_sources_batch.json"))


def service(
    coordinator: InMemoryRepositoryCoordinator,
    audit_sink: InMemoryEvidenceAuditSink | None = None,
) -> EvidenceProcessingService:
    return EvidenceProcessingService(
        coordinator,
        normalization=EvidenceNormalizationService(
            identity_resolver=ConfigurationIdentityResolver(
                service_aliases={"payments": "payment-api"},
                resource_aliases={"pay-1": "payment-api/instance-1"},
            )
        ),
        audit_sink=audit_sink,
    )


def log_batch(
    batch_id: str,
    source_record_ids: tuple[str, ...],
) -> RawEvidenceBatch:
    value = load("normalization/all_sources_batch.json")
    assert isinstance(value, dict)
    log_result = next(
        item for item in value["results"] if item["source_type"] == "logs"
    )
    template = log_result["records"][0]
    log_result["records"] = [
        {**template, "source_record_id": source_record_id}
        for source_record_id in source_record_ids
    ]
    value["batch_id"] = batch_id
    value["results"] = [log_result]
    return RawEvidenceBatch.from_dict(value)


class FailingDeduplicationRepository:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate

    def get_aggregates(self, evidence_ids: tuple[str, ...]) -> tuple[object, ...]:
        return self._delegate.get_aggregates(evidence_ids)  # type: ignore[attr-defined]

    def get_attachments(self, evidence_ids: tuple[str, ...]) -> tuple[object, ...]:
        return self._delegate.get_attachments(evidence_ids)  # type: ignore[attr-defined]

    def save_all(self, aggregates: tuple[object, ...], attachments: tuple[object, ...]) -> None:
        del aggregates, attachments
        raise RepositoryError("synthetic adapter failure")


class FailingAuditSink:
    def emit_all(self, events: tuple[object, ...]) -> None:
        del events
        raise RuntimeError("synthetic secret must not escape")


class FailingSecurityService:
    def secure_batch(self, normalized: object) -> object:
        del normalized
        raise RuntimeError("synthetic secret must not escape")


class EvidenceProcessingTests(unittest.TestCase):
    def test_mixed_source_batch_persists_complete_canonical_records(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        result = service(coordinator).process(incident(), (batch(),))
        records = coordinator.evidence.query(
            EvidenceFilter(
                schema_version="1.0",
                incident_id="inc_001",
                include_unknown_event_time=True,
                limit=100,
            )
        )
        self.assertEqual(len(result.saved_evidence_ids), 6)
        self.assertEqual(len(records), 6)
        self.assertEqual(
            {record.source_type for record in records},
            {"logs", "metrics", "changes", "deployments", "pipelines", "configuration"},
        )
        log = next(record for record in records if record.source_type == "logs")
        self.assertEqual(log.service, "payment-api")
        self.assertEqual(
            log.attributes["x-processing-metadata"]["resource"],
            "payment-api/instance-1",
        )
        self.assertEqual(
            log.to_dict(), load("application/persisted_log_record.json")
        )
        self.assertEqual(coordinator.evidence.get(log.evidence_id), log)

    def test_replaying_same_or_later_batch_keeps_evidence_count_and_ids(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = service(coordinator)
        first = processor.process(incident(), (batch(),))
        reordered = replace(batch(), results=tuple(reversed(batch().results)))
        identical = processor.process(incident(), (batch(), reordered))
        later_batch = replace(batch(), batch_id="batch_phase_3_replayed")
        later = processor.process(incident(), (later_batch,))
        stored = coordinator.evidence.query(
            EvidenceFilter(
                schema_version="1.0",
                incident_id="inc_001",
                include_unknown_event_time=True,
                limit=100,
            )
        )
        self.assertEqual(len(stored), 6)
        self.assertEqual(set(first.evidence_ids), {item.evidence_id for item in stored})
        self.assertEqual(identical.saved_evidence_ids, ())
        self.assertEqual(later.saved_evidence_ids, ())
        self.assertEqual(len(identical.reused_evidence_ids), 6)
        self.assertEqual(len(later.reused_evidence_ids), 6)

    def test_repeated_log_state_and_all_provenance_survive_replay(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = service(coordinator)
        first_batch = log_batch("batch_logs", ("log-a", "log-b"))
        first = processor.process(incident(), (first_batch,))
        evidence_id = first.saved_evidence_ids[0]
        aggregate = coordinator.deduplication.get_aggregates((evidence_id,))[0]
        self.assertEqual(aggregate.occurrence_count, 2)
        self.assertEqual(
            {item.source_record_id for item in aggregate.contributing_provenance},
            {"log-a", "log-b"},
        )

        replay = processor.process(incident(), (first_batch,))
        self.assertEqual(replay.saved_evidence_ids, ())
        self.assertEqual(replay.aggregated_evidence_ids, ())
        self.assertEqual(len(replay.reused_evidence_ids), 1)
        unchanged = coordinator.deduplication.get_aggregates((evidence_id,))[0]
        self.assertEqual(unchanged.occurrence_count, 2)

        third = processor.process(
            incident(), (log_batch("batch_logs_later", ("log-c",)),)
        )
        self.assertEqual(third.aggregated_evidence_ids, (evidence_id,))
        updated = coordinator.deduplication.get_aggregates((evidence_id,))[0]
        self.assertEqual(updated.occurrence_count, 3)
        self.assertEqual(
            {item.source_record_id for item in updated.contributing_provenance},
            {"log-a", "log-b", "log-c"},
        )

    def test_malformed_record_becomes_warning_without_losing_valid_siblings(self) -> None:
        value = load("normalization/all_sources_batch.json")
        assert isinstance(value, dict)
        value["results"][0]["records"].append(
            {
                "source_record_id": "log-malformed",
                "content_type": "application_log",
                "payload": ["not", "an", "object"],
            }
        )
        coordinator = InMemoryRepositoryCoordinator()
        result = service(coordinator).process(
            incident(), (RawEvidenceBatch.from_dict(value),)
        )
        self.assertEqual(len(result.saved_evidence_ids), 6)
        self.assertIn("malformed_record", {warning.code for warning in result.warnings})

    def test_source_errors_are_returned_but_do_not_abort_valid_records(self) -> None:
        value = load("normalization/all_sources_batch.json")
        assert isinstance(value, dict)
        value["errors"] = [
            {
                "schema_version": "1.0",
                "code": "source_failure",
                "message": "fixture source was partially unavailable",
                "retryable": True,
            }
        ]
        coordinator = InMemoryRepositoryCoordinator()
        result = service(coordinator).process(
            incident(), (RawEvidenceBatch.from_dict(value),)
        )
        self.assertEqual(len(result.saved_evidence_ids), 6)
        self.assertEqual(result.source_errors[0].code, "source_failure")

    def test_storage_failure_rolls_back_evidence_and_sidecars(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        original = coordinator.deduplication
        coordinator.deduplication = FailingDeduplicationRepository(  # type: ignore[assignment]
            original
        )
        with self.assertRaises(EvidenceProcessingError) as captured:
            service(coordinator).process(incident(), (batch(),))
        self.assertEqual(captured.exception.error.code, "storage_failure")
        self.assertEqual(
            coordinator.evidence.query(
                EvidenceFilter(schema_version="1.0", incident_id="inc_001")
            ),
            (),
        )
        self.assertEqual(original.get_attachments(tuple()), ())

    def test_concurrent_identical_requests_are_idempotent(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = service(coordinator)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(lambda _: processor.process(incident(), (batch(),)), range(2))
            )
        self.assertEqual(results[0].evidence_ids, results[1].evidence_ids)
        stored = coordinator.evidence.query(
            EvidenceFilter(
                schema_version="1.0",
                incident_id="inc_001",
                include_unknown_event_time=True,
                limit=100,
            )
        )
        self.assertEqual(len(stored), 6)

    def test_synthetic_secret_is_absent_from_storage_results_and_audit(self) -> None:
        value = load("normalization/all_sources_batch.json")
        assert isinstance(value, dict)
        value["results"] = [value["results"][0]]
        value["results"][0]["records"][0]["payload"]["api_key"] = (
            "synthetic-secret-value"
        )
        coordinator = InMemoryRepositoryCoordinator()
        audit = InMemoryEvidenceAuditSink()
        result = service(coordinator, audit).process(
            incident(), (RawEvidenceBatch.from_dict(value),)
        )
        stored = coordinator.evidence.get(result.saved_evidence_ids[0])
        serialized = json.dumps(
            {
                "record": stored.to_dict() if stored else None,
                "result": result.to_dict(),
                "audit": [event.to_dict() for event in audit.events],
            },
            sort_keys=True,
        )
        self.assertNotIn("synthetic-secret-value", serialized)
        self.assertTrue(stored and stored.quality.redactions_applied)
        self.assertIn("$.raw_payload.api_key", serialized)
        self.assertEqual(audit.events[0].event_type, "evidence_decision")

    def test_quarantined_private_key_never_reaches_evidence_storage(self) -> None:
        value = load("normalization/all_sources_batch.json")
        assert isinstance(value, dict)
        value["results"] = [value["results"][0]]
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "c3ludGhldGljLW5vdC1hLXJlYWwta2V5\n"
            "-----END PRIVATE KEY-----"
        )
        value["results"][0]["records"][0]["payload"]["message"] = private_key
        coordinator = InMemoryRepositoryCoordinator()
        audit = InMemoryEvidenceAuditSink()
        result = service(coordinator, audit).process(
            incident(), (RawEvidenceBatch.from_dict(value),)
        )
        self.assertEqual(result.saved_evidence_ids, ())
        self.assertEqual(len(result.quarantined), 1)
        self.assertEqual(
            coordinator.evidence.query(
                EvidenceFilter(schema_version="1.0", incident_id="inc_001")
            ),
            (),
        )
        self.assertNotIn(
            private_key,
            json.dumps(
                {
                    "result": result.to_dict(),
                    "audit": [event.to_dict() for event in audit.events],
                }
            ),
        )

    def test_audit_sink_failure_is_sanitized_and_does_not_undo_storage(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = EvidenceProcessingService(
            coordinator,
            normalization=EvidenceNormalizationService(),
            audit_sink=FailingAuditSink(),
        )
        result = processor.process(incident(), (log_batch("batch_log", ("log-a",)),))
        warning = next(
            item for item in result.warnings if item.code == "x-observability-failure"
        )
        self.assertNotIn("synthetic secret", warning.message)
        self.assertIsNotNone(coordinator.evidence.get(result.saved_evidence_ids[0]))

    def test_empty_or_cross_incident_input_is_fatal_before_storage(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = service(coordinator)
        with self.assertRaises(EvidenceProcessingError) as empty:
            processor.process(incident(), ())
        self.assertEqual(empty.exception.error.code, "invalid_envelope")
        with self.assertRaises(EvidenceProcessingError) as mismatch:
            processor.process(
                incident(), (replace(batch(), incident_id="inc_other"),)
            )
        self.assertEqual(mismatch.exception.error.code, "incident_mismatch")

    def test_conflicting_duplicate_batch_id_is_fatal_before_storage(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        conflicting = replace(
            batch(),
            plan_id="plan_conflicting",
        )
        with self.assertRaises(EvidenceProcessingError) as captured:
            service(coordinator).process(incident(), (batch(), conflicting))
        self.assertEqual(captured.exception.error.code, "invalid_envelope")
        self.assertEqual(
            coordinator.evidence.query(
                EvidenceFilter(schema_version="1.0", incident_id="inc_001")
            ),
            (),
        )

    def test_policy_failure_is_fatal_and_sanitized(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        processor = EvidenceProcessingService(
            coordinator,
            security=FailingSecurityService(),  # type: ignore[arg-type]
        )
        with self.assertRaises(EvidenceProcessingError) as captured:
            processor.process(incident(), (batch(),))
        self.assertEqual(captured.exception.error.code, "policy_rejected")
        self.assertNotIn("synthetic secret", captured.exception.error.message)
        self.assertEqual(
            coordinator.evidence.query(
                EvidenceFilter(schema_version="1.0", incident_id="inc_001")
            ),
            (),
        )

    def test_batch_order_does_not_change_canonical_output(self) -> None:
        first_batch = log_batch("batch_a", ("log-a",))
        second_batch = log_batch("batch_b", ("log-b",))
        first_coordinator = InMemoryRepositoryCoordinator()
        second_coordinator = InMemoryRepositoryCoordinator()
        first = service(first_coordinator).process(
            incident(), (first_batch, second_batch)
        )
        second = service(second_coordinator).process(
            incident(), (second_batch, first_batch)
        )
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
