"""Batch orchestration for classification and canonical normalization."""

from __future__ import annotations

from typing import Any, Mapping

from contracts.collection import RawEvidenceBatch, RawEvidenceRecord, SourceResult
from contracts.common import SCHEMA_VERSION, canonical_json, thaw_json
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.incident import IncidentSeed
from evidence.normalization.classifiers import (
    ClassifierRegistry,
    SourceClassifier,
    default_classifier_registry,
)
from evidence.normalization.identity import ConfigurationIdentityResolver, IdentityResolver
from evidence.normalization.models import (
    BatchNormalizationError,
    BatchNormalizationResult,
    NormalizedEvidenceCandidate,
    RecordNormalizationError,
)
from evidence.normalization.timestamps import TimestampNormalizer


class EvidenceNormalizationService:
    """Converts validated batches to candidates without persistence or security work."""

    def __init__(
        self,
        registry: ClassifierRegistry | None = None,
        timestamp_normalizer: TimestampNormalizer | None = None,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        self._registry = registry or default_classifier_registry()
        self._timestamps = timestamp_normalizer or TimestampNormalizer()
        self._identities = identity_resolver or ConfigurationIdentityResolver()

    def normalize(
        self, incident: IncidentSeed, batch: RawEvidenceBatch
    ) -> BatchNormalizationResult:
        if incident.incident_id != batch.incident_id:
            raise BatchNormalizationError(
                ProcessingError(
                    schema_version=SCHEMA_VERSION,
                    code="incident_mismatch",
                    message="raw evidence batch does not belong to the incident",
                    retryable=False,
                    details={
                        "incident_id": incident.incident_id,
                        "batch_incident_id": batch.incident_id,
                        "batch_id": batch.batch_id,
                    },
                )
            )

        candidates: list[NormalizedEvidenceCandidate] = []
        warnings: list[ProcessingWarning] = []
        ordered_results = sorted(
            batch.results, key=lambda result: (result.source_type, result.query_id)
        )
        for result in ordered_results:
            warnings.extend(result.warnings)
            classifier = self._registry.get(result.source_type)
            ordered_records = sorted(result.records, key=self._record_key)
            for raw_record in ordered_records:
                try:
                    candidates.append(
                        self._normalize_record(incident, batch, result, raw_record, classifier)
                    )
                except (RecordNormalizationError, TypeError, ValueError) as error:
                    warnings.append(
                        ProcessingWarning(
                            schema_version=SCHEMA_VERSION,
                            code="malformed_record",
                            message=str(error),
                            record_id=raw_record.source_record_id,
                            details={
                                "batch_id": batch.batch_id,
                                "query_id": result.query_id,
                                "source_type": result.source_type,
                            },
                        )
                    )
        return BatchNormalizationResult(
            incident_id=incident.incident_id,
            batch_id=batch.batch_id,
            candidates=tuple(candidates),
            warnings=tuple(warnings),
            errors=batch.errors,
        )

    @staticmethod
    def _record_key(record: RawEvidenceRecord) -> tuple[str, str, str]:
        return (
            record.source_record_id or "",
            record.content_type,
            canonical_json(record.payload),
        )

    def _normalize_record(
        self,
        incident: IncidentSeed,
        batch: RawEvidenceBatch,
        result: SourceResult,
        raw_record: RawEvidenceRecord,
        classifier: SourceClassifier,
    ) -> NormalizedEvidenceCandidate:
        payload_value = thaw_json(raw_record.payload)
        if not isinstance(payload_value, Mapping):
            raise RecordNormalizationError("record payload must be a JSON object")
        payload: Mapping[str, Any] = payload_value
        classified = classifier.classify(raw_record, payload)
        timestamps = self._timestamps.normalize(raw_record, batch.collected_at, payload)

        candidate_warnings: list[ProcessingWarning] = []
        service_input = classified.service or incident.service
        service = self._identities.resolve_service(service_input)
        if not service.matched:
            candidate_warnings.append(
                self._identity_warning("service", service.original, raw_record.source_record_id)
            )
        resource_id: str | None = None
        if classified.resource is not None:
            resource = self._identities.resolve_resource(classified.resource)
            resource_id = resource.canonical_id
            if not resource.matched:
                candidate_warnings.append(
                    self._identity_warning(
                        "resource", resource.original, raw_record.source_record_id
                    )
                )
        if timestamps.event_time is None or timestamps.event_time_approximate:
            candidate_warnings.append(
                ProcessingWarning(
                    schema_version=SCHEMA_VERSION,
                    code="time_uncertain",
                    message=(
                        "source event time is missing"
                        if timestamps.event_time is None
                        else "source event time is approximate"
                    ),
                    record_id=raw_record.source_record_id,
                    details={
                        "time_uncertainty_ms": timestamps.time_uncertainty_ms,
                    },
                )
            )
        return NormalizedEvidenceCandidate(
            schema_version=SCHEMA_VERSION,
            incident_id=incident.incident_id,
            batch_id=batch.batch_id,
            query_id=result.query_id,
            source_record_id=raw_record.source_record_id,
            source_adapter=result.source_adapter,
            source_type=result.source_type,
            source_status=result.source_status,
            source_truncated=result.truncated,
            evidence_type=classified.evidence_type,
            service=service.canonical_id,
            resource=resource_id,
            timestamps=timestamps,
            summary=classified.summary,
            attributes=classified.attributes,
            warnings=tuple(candidate_warnings),
            raw_payload=payload,
        )

    @staticmethod
    def _identity_warning(
        identity_type: str, original: str, record_id: str | None
    ) -> ProcessingWarning:
        return ProcessingWarning(
            schema_version=SCHEMA_VERSION,
            code="unknown_identity",
            message=f"unknown {identity_type} alias preserved as an explicit fallback",
            record_id=record_id,
            details={"identity_type": identity_type, "input": original},
        )
