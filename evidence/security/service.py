"""Orchestrates hashing, redaction, quarantine, provenance, and quality."""

from __future__ import annotations

from typing import Mapping

from contracts.common import SCHEMA_VERSION, thaw_json
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.evidence import EvidenceProvenance
from evidence.normalization.models import BatchNormalizationResult, NormalizedEvidenceCandidate
from evidence.quality import ConfigurableQualityPolicy, QualityPolicy
from evidence.security.models import (
    QuarantinedRecord,
    RedactionOutcome,
    RedactionResult,
    SecuredEvidenceCandidate,
    SecurityProcessingResult,
)
from evidence.security.provenance import ProvenanceBuilder
from evidence.security.redaction import RedactionPolicy, Redactor


class EvidenceSecurityError(RuntimeError):
    """Sanitized infrastructure failure at the evidence security boundary."""


class EvidenceSecurityService:
    """The only route from pre-security candidates to persistence-eligible data."""

    def __init__(
        self,
        *,
        provenance_builder: ProvenanceBuilder | None = None,
        redaction_policy: RedactionPolicy | None = None,
        quality_policy: QualityPolicy | None = None,
    ) -> None:
        self._provenance = provenance_builder or ProvenanceBuilder()
        self._redaction = redaction_policy or Redactor()
        self._quality = quality_policy or ConfigurableQualityPolicy()

    def secure_all(
        self,
        candidates: tuple[NormalizedEvidenceCandidate, ...],
        *,
        input_warnings: tuple[ProcessingWarning, ...] = (),
        input_errors: tuple[ProcessingError, ...] = (),
    ) -> SecurityProcessingResult:
        approved: list[SecuredEvidenceCandidate] = []
        quarantined: list[QuarantinedRecord] = []
        warnings = self._sanitize_input_warnings(input_warnings)
        errors = self._sanitize_input_errors(input_errors)
        for candidate in candidates:
            try:
                provenance = self._provenance.build(candidate)
            except Exception:
                raise EvidenceSecurityError("raw payload hashing failed") from None
            metadata_result = self._redact(
                {
                    "incident_id": candidate.incident_id,
                    "batch_id": candidate.batch_id,
                    "query_id": candidate.query_id,
                    "source_record_id": candidate.source_record_id,
                    "source_adapter": candidate.source_adapter,
                }
            )
            if metadata_result.outcome is not RedactionOutcome.PASS:
                quarantined.append(
                    self._quarantine_from_metadata(
                        candidate,
                        provenance,
                        metadata_result.value,
                        "sensitive_provenance_metadata",
                    )
                )
                warnings.append(
                    self._quarantine_warning(None, "sensitive_provenance_metadata")
                )
                continue

            redaction = self._redact(
                {
                    "service": candidate.service,
                    "resource": candidate.resource,
                    "summary": candidate.summary,
                    "attributes": candidate.attributes,
                    "warnings": [warning.to_dict() for warning in candidate.warnings],
                    "raw_payload": candidate.raw_payload,
                }
            )
            if redaction.outcome is RedactionOutcome.QUARANTINE:
                reason = redaction.reason_code or "policy_rejected"
                quarantined.append(self._quarantine(candidate, provenance, reason))
                warnings.append(
                    self._quarantine_warning(candidate.source_record_id, reason)
                )
                continue

            sanitized = thaw_json(redaction.value)
            if not isinstance(sanitized, Mapping):
                # A policy implementation violating its contract fails closed.
                quarantined.append(self._quarantine(candidate, provenance, "invalid_policy_output"))
                warnings.append(
                    self._quarantine_warning(
                        candidate.source_record_id, "invalid_policy_output"
                    )
                )
                continue
            try:
                quality = self._quality.assess(candidate, redaction.redactions_applied)
            except Exception:
                raise EvidenceSecurityError("evidence quality policy failed") from None
            candidate_warnings = self._sanitized_warnings(sanitized.get("warnings", []))
            candidate_warnings.extend(quality.warnings)
            if redaction.redactions_applied:
                candidate_warnings.append(
                    ProcessingWarning(
                        schema_version=SCHEMA_VERSION,
                        code="redacted",
                        message="sensitive content was redacted before the evidence boundary",
                        record_id=candidate.source_record_id,
                        details={"redaction_count": len(redaction.redacted_paths)},
                    )
                )
            attributes = sanitized.get("attributes")
            service = sanitized.get("service")
            summary = sanitized.get("summary")
            resource = sanitized.get("resource")
            if (
                not isinstance(attributes, Mapping)
                or not isinstance(service, str)
                or not service.strip()
                or not isinstance(summary, str)
                or not summary.strip()
                or (resource is not None and not isinstance(resource, str))
            ):
                quarantined.append(self._quarantine(candidate, provenance, "invalid_policy_output"))
                warnings.append(
                    self._quarantine_warning(
                        candidate.source_record_id, "invalid_policy_output"
                    )
                )
                continue
            approved.append(
                SecuredEvidenceCandidate(
                    schema_version=SCHEMA_VERSION,
                    incident_id=candidate.incident_id,
                    source_type=candidate.source_type,
                    source_status=candidate.source_status,
                    evidence_type=candidate.evidence_type,
                    service=service,
                    resource=resource,
                    timestamps=candidate.timestamps,
                    summary=summary,
                    attributes=attributes,
                    provenance=provenance,
                    quality=quality.quality,
                    quality_rationale=quality.rationale,
                    warnings=tuple(candidate_warnings),
                    redacted_paths=redaction.redacted_paths,
                )
            )
            # The sanitized raw payload is deliberately not copied to the safe candidate.
            del sanitized
        return SecurityProcessingResult(
            approved=tuple(approved),
            quarantined=tuple(quarantined),
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def secure_batch(self, result: BatchNormalizationResult) -> SecurityProcessingResult:
        return self.secure_all(
            result.candidates,
            input_warnings=result.warnings,
            input_errors=result.errors,
        )

    def _redact(self, value: object) -> RedactionResult:
        try:
            return self._redaction.redact(value)
        except Exception:
            raise EvidenceSecurityError("evidence redaction policy failed") from None

    def _sanitize_input_warnings(
        self, warnings: tuple[ProcessingWarning, ...]
    ) -> list[ProcessingWarning]:
        if not warnings:
            return []
        result = self._redact([warning.to_dict() for warning in warnings])
        if result.outcome is RedactionOutcome.QUARANTINE:
            return [
                ProcessingWarning(
                    schema_version=SCHEMA_VERSION,
                    code="x-quarantined",
                    message="an input warning was omitted by evidence security policy",
                )
            ]
        return self._sanitized_warnings(thaw_json(result.value))

    def _sanitize_input_errors(
        self, errors: tuple[ProcessingError, ...]
    ) -> list[ProcessingError]:
        if not errors:
            return []
        result = self._redact([error.to_dict() for error in errors])
        if result.outcome is RedactionOutcome.QUARANTINE:
            return [
                ProcessingError(
                    schema_version=SCHEMA_VERSION,
                    code="policy_rejected",
                    message="an input error was omitted by evidence security policy",
                    retryable=False,
                )
            ]
        value = thaw_json(result.value)
        if not isinstance(value, list):
            return []
        sanitized: list[ProcessingError] = []
        for item in value:
            try:
                sanitized.append(ProcessingError.from_dict(item))
            except (TypeError, ValueError):
                continue
        return sanitized

    @staticmethod
    def _sanitized_warnings(value: object) -> list[ProcessingWarning]:
        if not isinstance(value, list):
            return []
        warnings: list[ProcessingWarning] = []
        for item in value:
            try:
                warnings.append(ProcessingWarning.from_dict(item))
            except (TypeError, ValueError):
                # Fail closed without reflecting policy output into an exception.
                continue
        return warnings

    @staticmethod
    def _quarantine(
        candidate: NormalizedEvidenceCandidate,
        provenance: EvidenceProvenance,
        reason: str,
    ) -> QuarantinedRecord:
        return QuarantinedRecord(
            schema_version=SCHEMA_VERSION,
            incident_id=candidate.incident_id,
            batch_id=candidate.batch_id,
            query_id=candidate.query_id,
            source_record_id=candidate.source_record_id,
            raw_payload_hash=provenance.raw_payload_hash,
            reason_code=reason,
        )

    @staticmethod
    def _quarantine_from_metadata(
        candidate: NormalizedEvidenceCandidate,
        provenance: EvidenceProvenance,
        sanitized_metadata: object,
        reason: str,
    ) -> QuarantinedRecord:
        metadata = thaw_json(sanitized_metadata)
        if not isinstance(metadata, Mapping):
            metadata = {}

        def safe_identifier(name: str) -> str:
            value = metadata.get(name)
            return value if isinstance(value, str) and value.strip() else "[REDACTED]"

        source_record_id = metadata.get("source_record_id")
        if not isinstance(source_record_id, str) or not source_record_id.strip():
            source_record_id = None
        return QuarantinedRecord(
            schema_version=SCHEMA_VERSION,
            incident_id=safe_identifier("incident_id"),
            batch_id=safe_identifier("batch_id"),
            query_id=safe_identifier("query_id"),
            source_record_id=source_record_id,
            raw_payload_hash=provenance.raw_payload_hash,
            reason_code=reason,
        )

    @staticmethod
    def _quarantine_warning(
        record_id: str | None, reason: str
    ) -> ProcessingWarning:
        return ProcessingWarning(
            schema_version=SCHEMA_VERSION,
            code="x-quarantined",
            message="record was quarantined by evidence security policy",
            record_id=record_id,
            details={"reason_code": reason},
        )
