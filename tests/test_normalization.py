"""Phase 3 classifier, timestamp, identity, and isolation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from contracts.collection import RawEvidenceBatch, RawEvidenceRecord
from contracts.incident import IncidentSeed
from evidence.normalization import (
    BatchNormalizationError,
    ConfigurationIdentityResolver,
    EvidenceNormalizationService,
    TimestampNormalizer,
)


CONTRACT_FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
NORMALIZATION_FIXTURES = Path(__file__).parent / "fixtures" / "normalization"


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def incident() -> IncidentSeed:
    return IncidentSeed.from_dict(load(CONTRACT_FIXTURES / "incident_seed.json"))


def batch() -> RawEvidenceBatch:
    return RawEvidenceBatch.from_dict(
        load(NORMALIZATION_FIXTURES / "all_sources_batch.json")
    )


def service() -> EvidenceNormalizationService:
    return EvidenceNormalizationService(
        identity_resolver=ConfigurationIdentityResolver(
            service_aliases={"payments": "payment-api"},
            resource_aliases={"pay-1": "payment-api/instance-1"},
        )
    )


class SourceClassificationTests(unittest.TestCase):
    def test_all_six_sources_match_normalized_candidate_fixture(self) -> None:
        result = service().normalize(incident(), batch())
        actual = [
            {
                "source_type": item.source_type,
                "evidence_type": item.evidence_type,
                "summary": item.summary,
                "service": item.service,
                "source_status": item.source_status,
                "source_truncated": item.source_truncated,
                "attributes": item.to_dict()["attributes"],
            }
            for item in result.candidates
        ]
        self.assertEqual(
            actual, load(NORMALIZATION_FIXTURES / "normalized_candidates.json")
        )
        self.assertEqual(
            {item.source_type for item in result.candidates},
            {"logs", "metrics", "changes", "deployments", "pipelines", "configuration"},
        )

    def test_output_is_deterministic_when_result_and_record_order_changes(self) -> None:
        original = batch()
        reversed_batch = replace(original, results=tuple(reversed(original.results)))
        first = service().normalize(incident(), original)
        second = service().normalize(incident(), reversed_batch)
        self.assertEqual(
            [candidate.to_dict() for candidate in first.candidates],
            [candidate.to_dict() for candidate in second.candidates],
        )

    def test_partial_and_truncated_metadata_and_source_warning_are_preserved(self) -> None:
        result = service().normalize(incident(), batch())
        configuration = next(
            item for item in result.candidates if item.source_type == "configuration"
        )
        self.assertEqual(configuration.source_status, "partial")
        self.assertTrue(configuration.source_truncated)
        self.assertIn("partial_result", {warning.code for warning in result.warnings})

    def test_candidate_projection_never_returns_raw_payload(self) -> None:
        candidate = service().normalize(incident(), batch()).candidates[0]
        self.assertNotIn("raw_payload", candidate.to_dict())
        self.assertNotIn("raw_payload", repr(candidate))


class TimestampNormalizationTests(unittest.TestCase):
    def test_normalizes_offsets_and_preserves_available_original_metadata(self) -> None:
        offset = timezone(timedelta(hours=5, minutes=30))
        record = RawEvidenceRecord(
            source_record_id="log-offset",
            event_time=datetime(2026, 9, 12, 16, 0, tzinfo=offset),
            observed_at=datetime(2026, 9, 12, 16, 1, tzinfo=offset),
            content_type="application_log",
            payload={},
        )
        normalized = TimestampNormalizer().normalize(
            record, datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc), {}
        )
        self.assertEqual(
            normalized.event_time, datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(normalized.event_time_offset, "+05:30")
        self.assertEqual(normalized.event_time_original, "2026-09-12T16:00:00+05:30")

    def test_daylight_saving_boundaries_use_the_timestamp_specific_offset(self) -> None:
        new_york = ZoneInfo("America/New_York")
        normalizer = TimestampNormalizer()
        winter = RawEvidenceRecord(
            "winter",
            datetime(2026, 1, 15, 10, 0, tzinfo=new_york),
            None,
            "event",
            {},
        )
        summer = replace(
            winter,
            source_record_id="summer",
            event_time=datetime(2026, 7, 15, 10, 0, tzinfo=new_york),
        )
        collected = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        self.assertEqual(
            normalizer.normalize(winter, collected, {}).event_time,
            datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            normalizer.normalize(summer, collected, {}).event_time,
            datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        )

    def test_missing_and_approximate_times_remain_explicit(self) -> None:
        result = service().normalize(incident(), batch())
        configuration = next(
            item for item in result.candidates if item.source_type == "configuration"
        )
        deployment = next(
            item for item in result.candidates if item.source_type == "deployments"
        )
        self.assertIsNone(configuration.timestamps.event_time)
        self.assertIn("time_uncertain", {warning.code for warning in configuration.warnings})
        self.assertTrue(deployment.timestamps.event_time_approximate)
        self.assertEqual(deployment.timestamps.time_uncertainty_ms, 1000)
        self.assertIn("time_uncertain", {warning.code for warning in deployment.warnings})


class IdentityAndFailureTests(unittest.TestCase):
    def test_unknown_alias_is_preserved_with_an_explicit_warning(self) -> None:
        resolver = ConfigurationIdentityResolver(
            service_aliases={"payment-api": "payment-api"}
        )
        normalization = EvidenceNormalizationService(identity_resolver=resolver)
        source = batch().results[0]
        unknown_record = replace(
            source.records[0],
            payload={"level": "error", "message": "Failure", "service": "legacy-pay"},
        )
        one_result = replace(source, records=(unknown_record,))
        result = normalization.normalize(
            incident(), replace(batch(), results=(one_result,))
        )
        self.assertEqual(result.candidates[0].service, "legacy-pay")
        self.assertIn(
            "unknown_identity", {warning.code for warning in result.candidates[0].warnings}
        )

    def test_batch_incident_mismatch_returns_structured_error(self) -> None:
        with self.assertRaises(BatchNormalizationError) as raised:
            service().normalize(incident(), replace(batch(), incident_id="inc_other"))
        self.assertEqual(raised.exception.error.code, "incident_mismatch")
        self.assertFalse(raised.exception.error.retryable)

    def test_malformed_record_does_not_discard_valid_sibling(self) -> None:
        log_result = next(item for item in batch().results if item.source_type == "logs")
        malformed = RawEvidenceRecord(
            source_record_id="log-bad",
            event_time=None,
            observed_at=None,
            content_type="application_log",
            payload={"level": "error"},
        )
        mixed = replace(log_result, records=(malformed, log_result.records[0]))
        result = service().normalize(incident(), replace(batch(), results=(mixed,)))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source_record_id, "log-1")
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0].code, "malformed_record")
        self.assertEqual(result.warnings[0].record_id, "log-bad")

    def test_malformed_approximate_time_metadata_is_isolated(self) -> None:
        deployment_result = next(
            item for item in batch().results if item.source_type == "deployments"
        )
        record = deployment_result.records[0]
        payload = record.to_dict()["payload"]
        payload["time_uncertainty_ms"] = -1
        malformed = replace(record, payload=payload)
        result = service().normalize(
            incident(), replace(batch(), results=(replace(deployment_result, records=(malformed,)),))
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.warnings[0].code, "malformed_record")


if __name__ == "__main__":
    unittest.main(verbosity=2)
