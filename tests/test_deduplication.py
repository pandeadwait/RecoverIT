"""Phase 5 deterministic identity, reuse, and repeated-log aggregation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.evidence import EvidenceRecord
from contracts.incident import IncidentSeed
from evidence.deduplication import (
    DeduplicationAction,
    EvidenceDeduplicationService,
    LogAggregationPolicy,
)
from evidence.normalization import ConfigurationIdentityResolver, EvidenceNormalizationService
from evidence.security import EvidenceSecurityService
from evidence.security.models import SecuredEvidenceCandidate


FIXTURES = Path(__file__).parent / "fixtures"


def load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def approved() -> tuple[SecuredEvidenceCandidate, ...]:
    incident = IncidentSeed.from_dict(load("contracts/incident_seed.json"))
    batch = RawEvidenceBatch.from_dict(load("normalization/all_sources_batch.json"))
    normalized = EvidenceNormalizationService(
        identity_resolver=ConfigurationIdentityResolver(
            service_aliases={"payments": "payment-api"},
            resource_aliases={"pay-1": "payment-api/instance-1"},
        )
    ).normalize(incident, batch)
    return EvidenceSecurityService().secure_batch(normalized).approved


def source(source_type: str) -> SecuredEvidenceCandidate:
    return next(item for item in approved() if item.source_type == source_type)


def stored_record(evidence_id: str, candidate: SecuredEvidenceCandidate) -> EvidenceRecord:
    return EvidenceRecord(
        schema_version="1.0",
        evidence_id=evidence_id,
        incident_id=candidate.incident_id,
        source_type=candidate.source_type,
        evidence_type=candidate.evidence_type,
        service=candidate.service,
        event_time=candidate.timestamps.event_time,
        observed_at=candidate.timestamps.observed_at,
        collected_at=candidate.timestamps.collected_at,
        summary=candidate.summary,
        attributes=candidate.attributes,
        provenance=candidate.provenance,
        quality=candidate.quality,
    )


def alternate_provenance(
    candidate: SecuredEvidenceCandidate,
    *,
    source_record_id: str,
    batch_id: str = "batch_replay",
    query_id: str = "qry_replay",
) -> SecuredEvidenceCandidate:
    return replace(
        candidate,
        provenance=replace(
            candidate.provenance,
            batch_id=batch_id,
            query_id=query_id,
            source_record_id=source_record_id,
        ),
    )


class DeduplicationTests(unittest.TestCase):
    def test_same_batch_replay_creates_once_then_reuses_the_same_id(self) -> None:
        candidate = source("logs")
        result = EvidenceDeduplicationService().resolve((candidate,) * 5)
        self.assertEqual(
            tuple(item.action for item in result.decisions),
            (DeduplicationAction.CREATE,) + (DeduplicationAction.REUSE,) * 4,
        )
        self.assertEqual(len(result.creates), 1)
        self.assertEqual(result.decisions[0].evidence_id, result.decisions[1].evidence_id)
        self.assertEqual(len(result.provenance_attachments), 1)

    def test_same_source_record_in_different_batch_reuses_existing_evidence(self) -> None:
        candidate = source("logs")
        replay = alternate_provenance(
            candidate,
            source_record_id=candidate.provenance.source_record_id or "log-1",
            batch_id="batch_replayed",
        )
        existing = stored_record("ev_existing", candidate)
        result = EvidenceDeduplicationService().resolve((replay,), (existing,))
        self.assertEqual(result.decisions[0].action, DeduplicationAction.REUSE)
        self.assertEqual(result.decisions[0].evidence_id, "ev_existing")
        self.assertEqual(result.creates, ())
        self.assertEqual(result.provenance_attachments[0].provenance.batch_id, "batch_replayed")

    def test_repeated_logs_in_one_bucket_aggregate_and_preserve_both_provenances(self) -> None:
        log = source("logs")
        first = alternate_provenance(log, source_record_id="log-first", query_id="qry-first")
        second = alternate_provenance(log, source_record_id="log-second", query_id="qry-second")
        result = EvidenceDeduplicationService().resolve((second, first))
        self.assertEqual(
            {decision.action for decision in result.decisions},
            {DeduplicationAction.CREATE, DeduplicationAction.AGGREGATE},
        )
        self.assertEqual(len(result.creates), 1)
        self.assertEqual(len(result.aggregates), 1)
        aggregate = result.aggregates[0]
        self.assertEqual(aggregate.occurrence_count, 2)
        self.assertEqual(
            {item.source_record_id for item in aggregate.contributing_provenance},
            {"log-first", "log-second"},
        )
        self.assertEqual(aggregate.truncated_source, log.quality.truncated_source)

    def test_repeated_log_aggregates_against_existing_evidence(self) -> None:
        log = source("logs")
        repeated = alternate_provenance(
            log, source_record_id="log-repeated", query_id="qry-repeated"
        )
        existing = stored_record("ev_existing_log", log)
        result = EvidenceDeduplicationService().resolve((repeated,), (existing,))
        self.assertEqual(result.decisions[0].action, DeduplicationAction.AGGREGATE)
        self.assertEqual(result.decisions[0].evidence_id, "ev_existing_log")
        self.assertEqual(result.creates, ())
        self.assertEqual(result.aggregates[0].occurrence_count, 2)
        self.assertEqual(
            {item.source_record_id for item in result.aggregates[0].contributing_provenance},
            {log.provenance.source_record_id, "log-repeated"},
        )

    def test_log_identity_and_signature_are_opaque_hashes(self) -> None:
        log = source("logs")
        result = EvidenceDeduplicationService().resolve((log,))
        decision = result.decisions[0]
        self.assertIsNotNone(decision.aggregation_key_hash)
        self.assertRegex(decision.aggregation_key_hash or "", r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn(log.summary, decision.aggregation_key_hash or "")
        self.assertNotIn(log.summary, decision.evidence_id or "")

    def test_repeated_logs_outside_bucket_create_separate_evidence(self) -> None:
        log = source("logs")
        first = alternate_provenance(log, source_record_id="log-first")
        later_timestamps = replace(
            log.timestamps,
            event_time=log.timestamps.event_time + timedelta(seconds=301),
            observed_at=log.timestamps.observed_at + timedelta(seconds=301),
        )
        second = replace(
            alternate_provenance(log, source_record_id="log-later"),
            timestamps=later_timestamps,
        )
        result = EvidenceDeduplicationService().resolve((first, second))
        self.assertEqual(
            tuple(item.action for item in result.decisions),
            (DeduplicationAction.CREATE, DeduplicationAction.CREATE),
        )
        self.assertEqual(len(result.creates), 2)
        self.assertEqual(result.aggregates, ())

    def test_non_log_stable_external_identities_reuse_across_queries(self) -> None:
        for source_type in ("changes", "deployments", "pipelines", "configuration"):
            with self.subTest(source_type=source_type):
                candidate = source(source_type)
                second = alternate_provenance(
                    candidate,
                    source_record_id=f"{source_type}-replay",
                    query_id=f"qry-{source_type}-replay",
                )
                result = EvidenceDeduplicationService().resolve((candidate, second))
                self.assertEqual(len(result.creates), 1)
                self.assertEqual(
                    tuple(item.action for item in result.decisions),
                    (DeduplicationAction.CREATE, DeduplicationAction.REUSE),
                )
                self.assertEqual(result.decisions[1].reason, "stable_external_identity")

    def test_equal_non_log_content_with_different_external_ids_is_not_merged(self) -> None:
        deployment = source("deployments")
        other = replace(
            alternate_provenance(deployment, source_record_id="deploy-other"),
            attributes={
                "deployment_id": "deploy-other",
                "revision": "abc123",
                "status": "succeeded",
            },
        )
        result = EvidenceDeduplicationService().resolve((deployment, other))
        self.assertEqual(len(result.creates), 2)
        self.assertEqual(
            tuple(item.action for item in result.decisions),
            (DeduplicationAction.CREATE, DeduplicationAction.CREATE),
        )

    def test_existing_evidence_from_another_incident_is_ignored(self) -> None:
        candidate = source("logs")
        existing = replace(stored_record("ev_other", candidate), incident_id="inc_other")
        result = EvidenceDeduplicationService().resolve((candidate,), (existing,))
        self.assertEqual(result.decisions[0].action, DeduplicationAction.CREATE)

    def test_permuted_input_produces_identical_audit_output(self) -> None:
        log = source("logs")
        values = (
            alternate_provenance(log, source_record_id="log-a", query_id="qry-a"),
            alternate_provenance(log, source_record_id="log-b", query_id="qry-b"),
            source("deployments"),
        )
        service = EvidenceDeduplicationService()
        first = service.resolve(values)
        second = service.resolve(tuple(reversed(values)))
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_unsupported_source_is_rejected_without_an_evidence_id(self) -> None:
        candidate = replace(source("logs"), source_type="x-custom-source")
        result = EvidenceDeduplicationService().resolve((candidate,))
        self.assertEqual(result.decisions[0].action, DeduplicationAction.REJECT)
        self.assertIsNone(result.decisions[0].evidence_id)
        self.assertEqual(result.creates, ())

    def test_log_bucket_configuration_controls_aggregation_boundary(self) -> None:
        log = source("logs")
        first = alternate_provenance(log, source_record_id="log-first")
        second = replace(
            alternate_provenance(log, source_record_id="log-second"),
            timestamps=replace(
                log.timestamps,
                event_time=log.timestamps.event_time + timedelta(seconds=120),
            ),
        )
        default = EvidenceDeduplicationService().resolve((first, second))
        one_minute = EvidenceDeduplicationService(
            policy=LogAggregationPolicy(bucket_seconds=60)
        ).resolve((first, second))
        self.assertEqual(len(default.aggregates), 1)
        self.assertEqual(len(one_minute.aggregates), 0)
        self.assertEqual(len(one_minute.creates), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
