"""Phase 6 evidence normalization, security, deduplication, and persistence."""

from __future__ import annotations

from typing import Any, Mapping

from contracts.collection import RawEvidenceBatch
from contracts.common import SCHEMA_VERSION, canonical_bytes, thaw_json
from contracts.errors import ProcessingError, ProcessingWarning
from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.incident import IncidentSeed
from evidence.application.models import EvidenceProcessingError, EvidenceProcessingResult
from evidence.application.observability import (
    EvidenceAuditEvent,
    EvidenceAuditSink,
    NoOpEvidenceAuditSink,
)
from evidence.deduplication import (
    DeduplicationAction,
    DeduplicationResult,
    EvidenceDeduplicationService,
    IdentifiedEvidenceCandidate,
    RepeatedEventAggregate,
)
from evidence.normalization import EvidenceNormalizationService
from evidence.normalization.models import BatchNormalizationError
from evidence.repositories.coordinator import EvidenceBatchCoordinator
from evidence.repositories.ports import EvidenceOrder, EvidencePageRequest, RepositoryError
from evidence.security import EvidenceSecurityError, EvidenceSecurityService
from evidence.security.models import QuarantinedRecord, SecuredEvidenceCandidate


class EvidenceProcessingService:
    """Composes Phases 3–5 and persists only policy-approved evidence."""

    def __init__(
        self,
        coordinator: EvidenceBatchCoordinator,
        *,
        normalization: EvidenceNormalizationService | None = None,
        security: EvidenceSecurityService | None = None,
        deduplication: EvidenceDeduplicationService | None = None,
        audit_sink: EvidenceAuditSink | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._normalization = normalization or EvidenceNormalizationService()
        self._security = security or EvidenceSecurityService()
        self._deduplication = deduplication or EvidenceDeduplicationService()
        self._audit = audit_sink or NoOpEvidenceAuditSink()

    def process(
        self,
        incident: IncidentSeed,
        batches: tuple[RawEvidenceBatch, ...],
    ) -> EvidenceProcessingResult:
        ordered_batches = self._validate_and_order_batches(incident, batches)
        approved: list[SecuredEvidenceCandidate] = []
        quarantined: list[QuarantinedRecord] = []
        warnings: list[ProcessingWarning] = []
        source_errors: list[ProcessingError] = []

        for batch in ordered_batches:
            try:
                normalized = self._normalization.normalize(incident, batch)
                secured = self._security.secure_batch(normalized)
            except BatchNormalizationError as error:
                raise EvidenceProcessingError(error.error) from None
            except EvidenceSecurityError:
                raise self._policy_failure() from None
            except Exception:
                raise self._policy_failure() from None
            approved.extend(secured.approved)
            quarantined.extend(secured.quarantined)
            warnings.extend(secured.warnings)
            source_errors.extend(secured.errors)
            for candidate in secured.approved:
                warnings.extend(candidate.warnings)

        try:
            existing = self._load_existing(incident.incident_id)
            evidence_ids = tuple(record.evidence_id for record in existing)
            aggregates = self._coordinator.deduplication.get_aggregates(evidence_ids)
            attachments = self._coordinator.deduplication.get_attachments(evidence_ids)
        except RepositoryError as error:
            raise self._storage_failure(error.retryable) from None
        except Exception:
            raise self._storage_failure(True) from None

        try:
            deduplicated = self._deduplication.resolve(
                tuple(approved),
                existing,
                aggregates,
                attachments,
            )
            records = tuple(self._to_record(item) for item in deduplicated.creates)
        except EvidenceProcessingError:
            raise
        except Exception:
            raise self._policy_failure() from None

        try:
            saved_ids = self._coordinator.save_evidence_batch(
                records,
                deduplicated.aggregates,
                deduplicated.provenance_attachments,
            )
        except RepositoryError as error:
            raise self._storage_failure(error.retryable) from None
        except Exception:
            raise self._storage_failure(True) from None

        warnings.extend(self._rejection_warnings(deduplicated))
        batch_ids = tuple(batch.batch_id for batch in ordered_batches)
        audit_warning = self._emit_audit_events(
            incident.incident_id,
            batch_ids,
            deduplicated,
            tuple(quarantined),
        )
        if audit_warning is not None:
            warnings.append(audit_warning)

        return EvidenceProcessingResult(
            incident_id=incident.incident_id,
            batch_ids=batch_ids,
            saved_evidence_ids=tuple(sorted(set(saved_ids))),
            reused_evidence_ids=self._decision_ids(
                deduplicated, DeduplicationAction.REUSE
            ),
            aggregated_evidence_ids=self._decision_ids(
                deduplicated, DeduplicationAction.AGGREGATE
            ),
            warnings=self._unique_sorted(warnings),
            source_errors=self._unique_sorted(source_errors),
            quarantined=tuple(sorted(quarantined, key=canonical_bytes)),
            decisions=deduplicated.decisions,
            aggregates=deduplicated.aggregates,
            provenance_attachments=deduplicated.provenance_attachments,
        )

    @staticmethod
    def _validate_and_order_batches(
        incident: IncidentSeed,
        batches: tuple[RawEvidenceBatch, ...],
    ) -> tuple[RawEvidenceBatch, ...]:
        if not batches:
            raise EvidenceProcessingError(
                ProcessingError(
                    schema_version=SCHEMA_VERSION,
                    code="invalid_envelope",
                    message="at least one raw evidence batch is required",
                    retryable=False,
                )
            )
        unique: dict[str, RawEvidenceBatch] = {}
        for batch in batches:
            if batch.incident_id != incident.incident_id:
                raise EvidenceProcessingError(
                    ProcessingError(
                        schema_version=SCHEMA_VERSION,
                        code="incident_mismatch",
                        message="raw evidence batch does not belong to the incident",
                        retryable=False,
                        details={"batch_id": batch.batch_id},
                    )
                )
            existing = unique.get(batch.batch_id)
            if (
                existing is not None
                and EvidenceProcessingService._batch_content_key(existing)
                != EvidenceProcessingService._batch_content_key(batch)
            ):
                raise EvidenceProcessingError(
                    ProcessingError(
                        schema_version=SCHEMA_VERSION,
                        code="invalid_envelope",
                        message="batch ID is bound to conflicting collection content",
                        retryable=False,
                        details={"batch_id": batch.batch_id},
                    )
                )
            unique[batch.batch_id] = batch
        return tuple(unique[batch_id] for batch_id in sorted(unique))

    @staticmethod
    def _batch_content_key(batch: RawEvidenceBatch) -> bytes:
        value = batch.to_dict()
        for result in value["results"]:
            result["records"] = sorted(result["records"], key=canonical_bytes)
            result["warnings"] = sorted(result["warnings"], key=canonical_bytes)
        value["results"] = sorted(
            value["results"],
            key=lambda result: (result["source_type"], result["query_id"]),
        )
        value["errors"] = sorted(value["errors"], key=canonical_bytes)
        return canonical_bytes(value)

    def _load_existing(self, incident_id: str) -> tuple[EvidenceRecord, ...]:
        evidence_filter = EvidenceFilter(
            schema_version=SCHEMA_VERSION,
            incident_id=incident_id,
            include_unknown_event_time=True,
            limit=2_147_483_647,
        )
        records: list[EvidenceRecord] = []
        cursor: str | None = None
        while True:
            page = self._coordinator.evidence.query_page(
                evidence_filter,
                EvidencePageRequest(
                    limit=1000,
                    cursor=cursor,
                    order=EvidenceOrder.EVENT_TIME_ASC,
                ),
            )
            records.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return tuple(records)

    @staticmethod
    def _to_record(item: IdentifiedEvidenceCandidate) -> EvidenceRecord:
        candidate = item.candidate
        attributes = thaw_json(candidate.attributes)
        if not isinstance(attributes, Mapping):
            raise EvidenceProcessingError(
                ProcessingError(
                    schema_version=SCHEMA_VERSION,
                    code="policy_rejected",
                    message="approved evidence attributes violate the security contract",
                    retryable=False,
                )
            )
        persisted_attributes: dict[str, Any] = dict(attributes)
        persisted_attributes["x-processing-metadata"] = (
            EvidenceProcessingService._processing_metadata(candidate)
        )
        return EvidenceRecord(
            schema_version=SCHEMA_VERSION,
            evidence_id=item.evidence_id,
            incident_id=candidate.incident_id,
            source_type=candidate.source_type,
            evidence_type=candidate.evidence_type,
            service=candidate.service,
            event_time=candidate.timestamps.event_time,
            observed_at=candidate.timestamps.observed_at,
            collected_at=candidate.timestamps.collected_at,
            summary=candidate.summary,
            attributes=persisted_attributes,
            provenance=candidate.provenance,
            quality=candidate.quality,
        )

    @staticmethod
    def _processing_metadata(candidate: SecuredEvidenceCandidate) -> dict[str, Any]:
        timestamps = candidate.timestamps
        result: dict[str, Any] = {
            "event_time_approximate": timestamps.event_time_approximate,
            "quality_rationale": candidate.quality_rationale,
            "redacted_paths": list(candidate.redacted_paths),
        }
        for name in (
            "resource",
            "event_time_original",
            "observed_at_original",
            "event_time_offset",
            "observed_at_offset",
            "time_uncertainty_ms",
        ):
            value = (
                candidate.resource
                if name == "resource"
                else getattr(timestamps, name)
            )
            if value is not None:
                result[name] = value
        return result

    @staticmethod
    def _decision_ids(
        result: DeduplicationResult, action: DeduplicationAction
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    decision.evidence_id
                    for decision in result.decisions
                    if decision.action is action and decision.evidence_id is not None
                }
            )
        )

    @staticmethod
    def _rejection_warnings(
        result: DeduplicationResult,
    ) -> tuple[ProcessingWarning, ...]:
        return tuple(
            ProcessingWarning(
                schema_version=SCHEMA_VERSION,
                code="x-deduplication-rejected",
                message="safe candidate was rejected by deduplication policy",
                record_id=decision.provenance.source_record_id,
                details={"reason_code": decision.reason},
            )
            for decision in result.decisions
            if decision.action is DeduplicationAction.REJECT
        )

    def _emit_audit_events(
        self,
        incident_id: str,
        batch_ids: tuple[str, ...],
        result: DeduplicationResult,
        quarantined: tuple[QuarantinedRecord, ...],
    ) -> ProcessingWarning | None:
        events = [
            EvidenceAuditEvent(
                event_type="evidence_decision",
                incident_id=incident_id,
                batch_ids=batch_ids,
                evidence_id=decision.evidence_id,
                details={"action": decision.action.value, "reason": decision.reason},
            )
            for decision in result.decisions
        ]
        events.extend(
            EvidenceAuditEvent(
                event_type="evidence_quarantined",
                incident_id=incident_id,
                batch_ids=batch_ids,
                details={"reason_code": record.reason_code},
            )
            for record in quarantined
        )
        try:
            self._audit.emit_all(tuple(events))
            return None
        except Exception:
            return ProcessingWarning(
                schema_version=SCHEMA_VERSION,
                code="x-observability-failure",
                message="structured evidence audit event emission failed",
            )

    @staticmethod
    def _unique_sorted(values: list[Any]) -> tuple[Any, ...]:
        unique = {canonical_bytes(value): value for value in values}
        return tuple(unique[key] for key in sorted(unique))

    @staticmethod
    def _policy_failure() -> EvidenceProcessingError:
        return EvidenceProcessingError(
            ProcessingError(
                schema_version=SCHEMA_VERSION,
                code="policy_rejected",
                message="evidence processing policy failed safely",
                retryable=False,
            )
        )

    @staticmethod
    def _storage_failure(retryable: bool) -> EvidenceProcessingError:
        return EvidenceProcessingError(
            ProcessingError(
                schema_version=SCHEMA_VERSION,
                code="storage_failure",
                message="evidence persistence failed",
                retryable=retryable,
            )
        )
