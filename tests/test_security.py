"""Phase 4 redaction, quarantine, provenance, and quality tests."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import unittest

from contracts.collection import RawEvidenceBatch
from contracts.common import canonical_json, thaw_json
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.incident import IncidentSeed
from contracts.primitives import FixedClock
from evidence.normalization import ConfigurationIdentityResolver, EvidenceNormalizationService
from evidence.quality import ConfigurableQualityPolicy
from evidence.security import (
    EvidenceSecurityError,
    EvidenceSecurityService,
    PatternRedactionPolicy,
    ProvenanceBuilder,
    QuarantinedRecord,
    RedactionOutcome,
    Redactor,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load(relative: str) -> object:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def candidates():
    incident = IncidentSeed.from_dict(load("contracts/incident_seed.json"))
    batch = RawEvidenceBatch.from_dict(load("normalization/all_sources_batch.json"))
    normalizer = EvidenceNormalizationService(
        identity_resolver=ConfigurationIdentityResolver(
            service_aliases={"payments": "payment-api"},
            resource_aliases={"pay-1": "payment-api/instance-1"},
        )
    )
    return normalizer.normalize(incident, batch).candidates


class RedactionTests(unittest.TestCase):
    def test_nested_free_text_keys_and_encoded_secrets_are_redacted(self) -> None:
        fixture = load("security/synthetic_secrets.json")
        policy = PatternRedactionPolicy(
            personal_identifiers=("synthetic.person@example.invalid",)
        )
        result = Redactor(policy).redact(fixture)
        serialized = canonical_json(result.value)
        self.assertEqual(result.outcome, RedactionOutcome.REDACT)
        self.assertTrue(result.redactions_applied)
        for secret in (
            "synthetic-password-123",
            "synthetic.token.value-12345",
            "synthetic-db-pass",
            "AKIAABCDEFGHIJKLMNOP",
            "synthetic.person@example.invalid",
            "cGFzc3dvcmQ9c3ludGhldGljLWVuY29kZWQtcGFzcw==",
        ):
            self.assertNotIn(secret, serialized)

    def test_configured_sensitive_field_is_redacted(self) -> None:
        result = Redactor(
            PatternRedactionPolicy(sensitive_field_names=("customer_reference",))
        ).redact({"customer_reference": "synthetic-person-42", "safe": "visible"})
        self.assertEqual(thaw_json(result.value)["customer_reference"], "[REDACTED]")
        self.assertEqual(thaw_json(result.value)["safe"], "visible")

    def test_pass_outcome_preserves_non_sensitive_content(self) -> None:
        value = {"status": "healthy", "count": 3}
        result = Redactor().redact(value)
        self.assertEqual(result.outcome, RedactionOutcome.PASS)
        self.assertEqual(thaw_json(result.value), value)


class SecurityBoundaryTests(unittest.TestCase):
    def test_approved_candidate_is_redacted_and_keeps_complete_provenance(self) -> None:
        original = candidates()[3]
        secret = "synthetic.token.value-12345"
        unsafe = replace(
            original,
            summary=f"Request used Bearer {secret}",
            attributes={"authorization": f"Bearer {secret}"},
            raw_payload={"authorization": f"Bearer {secret}"},
        )
        result = EvidenceSecurityService().secure_all((unsafe,))
        self.assertEqual(result.quarantined, ())
        self.assertEqual(len(result.approved), 1)
        secured = result.approved[0]
        serialized = canonical_json(secured.to_dict())
        self.assertNotIn(secret, serialized)
        self.assertTrue(secured.quality.redactions_applied)
        self.assertEqual(secured.provenance.batch_id, unsafe.batch_id)
        self.assertEqual(secured.provenance.query_id, unsafe.query_id)
        self.assertEqual(secured.provenance.source_record_id, unsafe.source_record_id)
        self.assertEqual(secured.provenance.source_adapter, unsafe.source_adapter)
        self.assertTrue(secured.provenance.raw_payload_hash.startswith("sha256:"))

    def test_private_key_is_quarantined_and_never_approved_or_logged(self) -> None:
        secret = "synthetic-private-key-body"
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            f"{secret}\n"
            "-----END PRIVATE KEY-----"
        )
        unsafe = replace(
            candidates()[3],
            summary="A key was printed",
            attributes={"diagnostic": private_key},
            raw_payload={"diagnostic": private_key},
        )
        with self.assertLogs("phase4-security-test", level="INFO") as captured:
            result = EvidenceSecurityService().secure_all((unsafe,))
            logging.getLogger("phase4-security-test").info("security result: %r", result)
        self.assertEqual(result.approved, ())
        self.assertEqual(len(result.quarantined), 1)
        self.assertEqual(result.quarantined[0].reason_code, "private_key_material")
        outputs = canonical_json(result.quarantined[0].to_dict()) + "".join(captured.output)
        self.assertNotIn(secret, outputs)
        self.assertNotIn(private_key, outputs)

    def test_encoded_private_key_is_also_quarantined(self) -> None:
        private_key = (
            "-----BEGIN PRIVATE KEY-----\nsynthetic-encoded-body\n"
            "-----END PRIVATE KEY-----"
        )
        encoded = b64encode(private_key.encode("utf-8")).decode("ascii")
        unsafe = replace(candidates()[0], raw_payload={"diagnostic": encoded})
        result = EvidenceSecurityService().secure_all((unsafe,))
        self.assertEqual(result.approved, ())
        self.assertEqual(result.quarantined[0].reason_code, "private_key_material")

    def test_sensitive_provenance_identifier_is_sanitized_in_quarantine(self) -> None:
        secret = "synthetic.token.value-12345"
        unsafe = replace(candidates()[0], source_record_id=secret)
        result = EvidenceSecurityService().secure_all((unsafe,))
        serialized = canonical_json(
            {
                "quarantined": [item.to_dict() for item in result.quarantined],
                "warnings": [item.to_dict() for item in result.warnings],
            }
        )
        self.assertEqual(result.approved, ())
        self.assertNotIn(secret, serialized)
        self.assertEqual(
            result.quarantined[0].reason_code, "sensitive_provenance_metadata"
        )

    def test_hash_is_stable_for_canonical_equivalent_raw_payload(self) -> None:
        original = candidates()[0]
        first = replace(original, raw_payload={"b": 2, "a": {"y": 1, "x": 0}})
        second = replace(original, raw_payload={"a": {"x": 0, "y": 1}, "b": 2})
        builder = ProvenanceBuilder()
        self.assertEqual(
            builder.build(first).raw_payload_hash,
            builder.build(second).raw_payload_hash,
        )

    def test_hashing_failure_uses_a_sanitized_exception(self) -> None:
        secret = "synthetic-hasher-secret"

        class FailingHasher:
            def digest(self, payload: bytes) -> str:
                raise RuntimeError(secret)

        service = EvidenceSecurityService(
            provenance_builder=ProvenanceBuilder(FailingHasher())
        )
        with self.assertRaises(EvidenceSecurityError) as raised:
            service.secure_all((candidates()[0],))
        self.assertNotIn(secret, str(raised.exception))

    def test_redaction_policy_failure_uses_a_sanitized_exception(self) -> None:
        secret = "synthetic-policy-secret"

        class FailingPolicy:
            def redact(self, value: object):
                raise RuntimeError(secret)

        service = EvidenceSecurityService(redaction_policy=FailingPolicy())
        with self.assertRaises(EvidenceSecurityError) as raised:
            service.secure_all((candidates()[0],))
        self.assertNotIn(secret, str(raised.exception))

    def test_batch_level_warnings_and_errors_are_redacted(self) -> None:
        secret = "synthetic.token.value-12345"
        warning = ProcessingWarning(
            schema_version="1.0",
            code="partial_result",
            message=f"upstream returned Bearer {secret}",
        )
        error = ProcessingError(
            schema_version="1.0",
            code="source_failure",
            message="source used postgresql://user:synthetic-pass@db.invalid/app",
            retryable=True,
        )
        result = EvidenceSecurityService().secure_all(
            (), input_warnings=(warning,), input_errors=(error,)
        )
        serialized = canonical_json(
            {
                "warnings": [item.to_dict() for item in result.warnings],
                "errors": [item.to_dict() for item in result.errors],
            }
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn("synthetic-pass", serialized)
        self.assertEqual(result.warnings[0].code, "partial_result")
        self.assertEqual(result.errors[0].code, "source_failure")

    def test_quarantine_contract_round_trips_without_content(self) -> None:
        private_key = (
            "-----BEGIN PRIVATE KEY-----\nsynthetic-body\n-----END PRIVATE KEY-----"
        )
        unsafe = replace(
            candidates()[0],
            raw_payload={"private_key": private_key},
        )
        quarantined = EvidenceSecurityService().secure_all((unsafe,)).quarantined[0]
        self.assertEqual(QuarantinedRecord.from_dict(quarantined.to_dict()), quarantined)
        self.assertNotIn("synthetic-body", canonical_json(quarantined.to_dict()))

    def test_raw_payload_is_absent_from_every_phase_4_output(self) -> None:
        result = EvidenceSecurityService().secure_all(candidates())
        for secured in result.approved:
            self.assertNotIn("raw_payload", secured.to_dict())
            self.assertNotIn("raw_payload=", repr(secured))
        for quarantined in result.quarantined:
            self.assertNotIn("raw_payload", quarantined.to_dict())


class QualityPolicyTests(unittest.TestCase):
    def test_reliability_rules_truncation_and_freshness_are_explainable(self) -> None:
        configuration = candidates()[1]
        policy = ConfigurableQualityPolicy(
            reliability_by_source={"configuration": "high"},
            default_reliability="low",
            freshness_reference="collected_at",
        )
        assessment = policy.assess(configuration, redactions_applied=False)
        self.assertEqual(assessment.quality.reliability, "medium")
        self.assertTrue(assessment.quality.truncated_source)
        self.assertEqual(assessment.quality.freshness_seconds, 779)
        self.assertIn("source_type:configuration", assessment.rationale)
        self.assertIn("degraded one level", assessment.rationale)

    def test_evidence_type_rule_overrides_source_rule(self) -> None:
        log = candidates()[3]
        policy = ConfigurableQualityPolicy(
            reliability_by_source={"logs": "low"},
            reliability_by_evidence_type={"error_event": "high"},
        )
        assessment = policy.assess(log, redactions_applied=True)
        self.assertEqual(assessment.quality.reliability, "high")
        self.assertTrue(assessment.quality.redactions_applied)
        self.assertIn("evidence_type:error_event", assessment.rationale)

    def test_clock_skew_clamps_freshness_and_emits_safe_warning(self) -> None:
        log = candidates()[3]
        future_observation = replace(
            log.timestamps,
            observed_at=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
        )
        log = replace(log, timestamps=future_observation)
        policy = ConfigurableQualityPolicy(
            freshness_reference="clock",
            clock=FixedClock(datetime(2026, 9, 12, 11, 59, 55, tzinfo=timezone.utc)),
        )
        assessment = policy.assess(log, redactions_applied=False)
        self.assertEqual(assessment.quality.freshness_seconds, 0)
        self.assertEqual(assessment.warnings[0].code, "x-clock-skew")
        self.assertEqual(assessment.warnings[0].to_dict()["details"]["skew_seconds"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
