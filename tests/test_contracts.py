"""Comprehensive Phase 1 contract tests using only the Python standard library."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.common import ContractValidationError, canonical_bytes, canonical_json
from contracts.context import IncidentContextSnapshot
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.evidence import EvidenceFilter, EvidenceProvenance, EvidenceQuality, EvidenceRecord
from contracts.incident import IncidentSeed
from contracts.primitives import DeterministicIdGenerator, FixedClock, Sha256PayloadHasher
from contracts.timeline import TemporalRelationship, Timeline, TimelineEvent


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractRoundTripTests(unittest.TestCase):
    def assert_round_trip(self, contract_type: object, fixture_name: str) -> None:
        data = fixture(fixture_name)
        parsed = contract_type.from_dict(data)  # type: ignore[attr-defined]
        self.assertEqual(parsed.to_dict(), data)
        self.assertEqual(contract_type.from_dict(parsed.to_dict()), parsed)  # type: ignore[attr-defined]

    def test_incident_seed_round_trips(self) -> None:
        self.assert_round_trip(IncidentSeed, "incident_seed.json")

    def test_raw_evidence_batch_round_trips(self) -> None:
        self.assert_round_trip(RawEvidenceBatch, "raw_evidence_batch.json")

    def test_evidence_record_round_trips(self) -> None:
        self.assert_round_trip(EvidenceRecord, "evidence_record.json")

    def test_evidence_provenance_round_trips(self) -> None:
        self.assert_round_trip(EvidenceProvenance, "evidence_provenance.json")

    def test_evidence_quality_round_trips(self) -> None:
        self.assert_round_trip(EvidenceQuality, "evidence_quality.json")

    def test_evidence_filter_round_trips(self) -> None:
        self.assert_round_trip(EvidenceFilter, "evidence_filter.json")

    def test_timeline_event_round_trips(self) -> None:
        self.assert_round_trip(TimelineEvent, "timeline_event.json")

    def test_temporal_relationship_round_trips(self) -> None:
        self.assert_round_trip(TemporalRelationship, "temporal_relationship.json")

    def test_timeline_round_trips(self) -> None:
        data = fixture("timeline.json")
        parsed = Timeline.from_dict(data["incident_id"], data["events"], data["relationships"])
        self.assertEqual(parsed.to_dict(), {"events": data["events"], "relationships": data["relationships"]})

    def test_processing_warning_round_trips(self) -> None:
        self.assert_round_trip(ProcessingWarning, "processing_warning.json")

    def test_processing_error_round_trips(self) -> None:
        self.assert_round_trip(ProcessingError, "processing_error.json")

    def test_incident_context_snapshot_round_trips(self) -> None:
        self.assert_round_trip(IncidentContextSnapshot, "incident_context_snapshot.json")

    def test_all_output_fixtures_are_json_serializable(self) -> None:
        contracts = (
            (EvidenceRecord, "evidence_record.json"),
            (TimelineEvent, "timeline_event.json"),
            (TemporalRelationship, "temporal_relationship.json"),
            (IncidentContextSnapshot, "incident_context_snapshot.json"),
            (ProcessingWarning, "processing_warning.json"),
            (ProcessingError, "processing_error.json"),
        )
        for contract_type, fixture_name in contracts:
            with self.subTest(fixture=fixture_name):
                wire = contract_type.from_dict(fixture(fixture_name)).to_dict()
                self.assertEqual(json.loads(json.dumps(wire)), wire)


class ValidationTests(unittest.TestCase):
    def test_rejects_unsupported_schema_version(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "unsupported_schema_version"):
            EvidenceRecord.from_dict(fixture("invalid_schema_version.json"))

    def test_rejects_missing_required_evidence_id(self) -> None:
        data = fixture("evidence_record.json")
        del data["evidence_id"]
        with self.assertRaisesRegex(ContractValidationError, "invalid_type"):
            EvidenceRecord.from_dict(data)

    def test_rejects_timezone_less_timestamp(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "timezone_required"):
            IncidentSeed.from_dict(fixture("invalid_timezone.json"))

    def test_rejects_malformed_timestamp(self) -> None:
        data = fixture("incident_seed.json")
        data["detected_at"] = "not-a-timestamp"
        with self.assertRaisesRegex(ContractValidationError, "invalid_timestamp"):
            IncidentSeed.from_dict(data)

    def test_normalizes_timezones_to_utc_on_output(self) -> None:
        data = fixture("incident_seed.json")
        data["detected_at"] = "2026-09-12T16:00:00+05:30"
        incident = IncidentSeed.from_dict(data)
        self.assertEqual(incident.to_dict()["detected_at"], "2026-09-12T10:30:00Z")

    def test_supported_x_extension_is_preserved(self) -> None:
        data = fixture("evidence_record.json")
        data["source_type"] = "x-security-scanner"
        evidence = EvidenceRecord.from_dict(data)
        self.assertEqual(evidence.source_type, "x-security-scanner")

    def test_rejects_unregistered_non_extension_enum(self) -> None:
        data = fixture("evidence_record.json")
        data["source_type"] = "security_scanner"
        with self.assertRaisesRegex(ContractValidationError, "unsupported_enum"):
            EvidenceRecord.from_dict(data)

    def test_ignores_additive_unknown_optional_fields(self) -> None:
        data = fixture("evidence_record.json")
        data["future_optional_field"] = {"producer": "person-1"}
        parsed = EvidenceRecord.from_dict(data)
        self.assertNotIn("future_optional_field", parsed.to_dict())

    def test_rejects_non_json_attributes(self) -> None:
        data = fixture("evidence_record.json")
        data["attributes"] = {"invalid": object()}
        with self.assertRaisesRegex(ContractValidationError, "invalid_json"):
            EvidenceRecord.from_dict(data)

    def test_rejects_invalid_filter_time_window(self) -> None:
        data = fixture("evidence_filter.json")
        data["start_time"] = "2026-09-12T10:40:00Z"
        with self.assertRaisesRegex(ValueError, "start_time"):
            EvidenceFilter.from_dict(data)

    def test_rejects_empty_timeline_event_evidence_ids(self) -> None:
        data = fixture("timeline_event.json")
        data["evidence_ids"] = []
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            TimelineEvent.from_dict(data)

    def test_rejects_timeline_relationship_with_unknown_endpoint(self) -> None:
        event = TimelineEvent.from_dict(fixture("timeline_event.json"))
        relationship = TemporalRelationship(
            schema_version="1.0",
            relationship_id="rel_001",
            incident_id="inc_001",
            from_event_id="tle_301",
            to_event_id="tle_missing",
            relationship_type="PRECEDES",
            created_by="deterministic",
        )
        with self.assertRaisesRegex(ValueError, "existing timeline events"):
            Timeline(incident_id="inc_001", events=(event,), relationships=(relationship,))

    def test_rejects_cross_incident_snapshot_timeline(self) -> None:
        snapshot = IncidentContextSnapshot.from_dict(fixture("incident_context_snapshot.json"))
        cross_incident_timeline = Timeline(
            incident_id="inc_other",
            events=(),
            relationships=(),
        )
        with self.assertRaisesRegex(ValueError, "snapshot timeline"):
            replace(snapshot, timeline=cross_incident_timeline)

    def test_rejects_snapshot_event_reference_missing_from_projection(self) -> None:
        data = fixture("incident_context_snapshot.json")
        data["timeline"][0]["evidence_ids"] = ["ev_missing"]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "reference evidence"):
            IncidentContextSnapshot.from_dict(data)

    def test_rejects_incomplete_source_coverage(self) -> None:
        data = fixture("incident_context_snapshot.json")
        del data["source_coverage"]["metrics"]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "missing required sources"):
            IncidentContextSnapshot.from_dict(data)

    def test_rejects_unknown_source_coverage_state(self) -> None:
        data = fixture("incident_context_snapshot.json")
        data["source_coverage"]["logs"] = "investigating"  # type: ignore[index]
        with self.assertRaisesRegex(ContractValidationError, "unsupported_enum"):
            IncidentContextSnapshot.from_dict(data)

    def test_rejects_unavailable_source_result_with_records(self) -> None:
        data = fixture("raw_evidence_batch.json")
        data["results"][0]["source_status"] = "unavailable"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unavailable"):
            RawEvidenceBatch.from_dict(data)


class ImmutabilityAndDeterminismTests(unittest.TestCase):
    def test_evidence_attributes_are_immutable(self) -> None:
        evidence = EvidenceRecord.from_dict(fixture("evidence_record.json"))
        with self.assertRaises(TypeError):
            evidence.attributes["level"] = "warning"  # type: ignore[index]

    def test_evidence_identity_and_provenance_are_immutable(self) -> None:
        evidence = EvidenceRecord.from_dict(fixture("evidence_record.json"))
        with self.assertRaisesRegex(AttributeError, "evidence_id"):
            evidence.evidence_id = "ev_other"  # type: ignore[misc]
        with self.assertRaisesRegex(AttributeError, "source_adapter"):
            evidence.provenance.source_adapter = "other-adapter"  # type: ignore[misc]

    def test_snapshot_coverage_is_immutable(self) -> None:
        snapshot = IncidentContextSnapshot.from_dict(fixture("incident_context_snapshot.json"))
        with self.assertRaises(TypeError):
            snapshot.source_coverage["logs"] = "empty"  # type: ignore[index]

    def test_canonical_json_is_order_independent(self) -> None:
        first = {"nested": {"b": 2, "a": 1}, "name": "payment"}
        second = {"name": "payment", "nested": {"a": 1, "b": 2}}
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))

    def test_deterministic_id_generator_is_stable_and_input_sensitive(self) -> None:
        generator = DeterministicIdGenerator()
        first = generator.create("ev", {"id": "1", "service": "payment-api"})
        reordered = generator.create("ev", {"service": "payment-api", "id": "1"})
        different = generator.create("ev", {"id": "2", "service": "payment-api"})
        self.assertEqual(first, reordered)
        self.assertNotEqual(first, different)
        self.assertTrue(first.startswith("ev_"))

    def test_sha256_payload_hasher_is_stable_and_does_not_return_payload(self) -> None:
        hasher = Sha256PayloadHasher()
        secret_payload = b"synthetic-secret-value"
        digest = hasher.digest(secret_payload)
        self.assertEqual(digest, hasher.digest(secret_payload))
        self.assertTrue(digest.startswith("sha256:"))
        self.assertNotIn("synthetic-secret-value", digest)

    def test_fixed_clock_returns_the_configured_utc_instant(self) -> None:
        clock = FixedClock(datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(clock.now(), datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc))

    def test_fixed_contract_input_serializes_identically_on_repeated_runs(self) -> None:
        first = IncidentContextSnapshot.from_dict(fixture("incident_context_snapshot.json"))
        second = IncidentContextSnapshot.from_dict(fixture("incident_context_snapshot.json"))
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
