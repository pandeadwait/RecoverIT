"""Deterministic, storage-neutral deduplication and repeated-log aggregation."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from contracts.common import canonical_bytes, thaw_json
from contracts.evidence import EvidenceRecord
from evidence.deduplication.identity import (
    DeterministicEvidenceIdStrategy,
    EvidenceIdStrategy,
)
from evidence.deduplication.models import (
    DeduplicationAction,
    DeduplicationDecision,
    DeduplicationResult,
    IdentifiedEvidenceCandidate,
    ProvenanceAttachment,
    RepeatedEventAggregate,
)
from evidence.deduplication.policy import DeduplicationPolicy, LogAggregationPolicy
from evidence.security.models import SecuredEvidenceCandidate


_SUPPORTED_SOURCES = {
    "logs",
    "metrics",
    "changes",
    "deployments",
    "pipelines",
    "configuration",
}


class EvidenceDeduplicationService:
    """Resolves safe candidates without writing repositories or dropping provenance."""

    def __init__(
        self,
        *,
        id_strategy: EvidenceIdStrategy | None = None,
        policy: DeduplicationPolicy | None = None,
    ) -> None:
        self._ids = id_strategy or DeterministicEvidenceIdStrategy()
        self._policy = policy or LogAggregationPolicy()

    def resolve(
        self,
        candidates: tuple[SecuredEvidenceCandidate, ...],
        existing_matches: tuple[EvidenceRecord, ...] = (),
    ) -> DeduplicationResult:
        incident_ids = {candidate.incident_id for candidate in candidates}
        if len(incident_ids) > 1:
            raise ValueError(
                "deduplication resolve accepts candidates for one incident only"
            )
        incident_id = next(iter(incident_ids), None)
        exact_index: dict[tuple[str, str, str], str] = {}
        stable_index: dict[tuple[str, str], str] = {}
        log_index: dict[tuple[str, str, int], str] = {}
        aggregate_state: dict[str, RepeatedEventAggregate] = {}
        known_ids: set[str] = set()

        for record in sorted(existing_matches, key=lambda item: item.evidence_id):
            if incident_id is not None and record.incident_id != incident_id:
                continue
            known_ids.add(record.evidence_id)
            if record.source_type not in _SUPPORTED_SOURCES:
                continue
            exact = self._exact_key_for_record(record)
            if exact is not None:
                exact_index.setdefault(exact, record.evidence_id)
            stable = self._stable_key_for_record(record)
            if stable is not None:
                stable_index.setdefault(stable, record.evidence_id)
            log_key = self._log_key_for_record(record)
            if log_key is not None:
                log_index.setdefault(log_key, record.evidence_id)
                aggregate_state.setdefault(
                    record.evidence_id, self._aggregate_from_record(record, log_key)
                )

        creates: dict[str, IdentifiedEvidenceCandidate] = {}
        decisions: list[DeduplicationDecision] = []
        attachments: list[ProvenanceAttachment] = []
        updated_aggregate_ids: set[str] = set()

        for candidate in sorted(candidates, key=self._candidate_sort_key):
            if candidate.source_type not in _SUPPORTED_SOURCES:
                decisions.append(
                    DeduplicationDecision(
                        action=DeduplicationAction.REJECT,
                        evidence_id=None,
                        reason="unsupported_source_type",
                        provenance=candidate.provenance,
                    )
                )
                continue

            exact = self._policy.exact_key(candidate)
            if exact is not None and exact in exact_index:
                evidence_id = exact_index[exact]
                decisions.append(
                    self._decision(
                        DeduplicationAction.REUSE,
                        evidence_id,
                        "exact_source_record_and_payload",
                        candidate,
                    )
                )
                attachments.append(self._attachment(evidence_id, candidate, "reused_exact"))
                continue

            stable = self._stable_key_for_candidate(candidate)
            if stable is not None and stable in stable_index:
                evidence_id = stable_index[stable]
                decisions.append(
                    self._decision(
                        DeduplicationAction.REUSE,
                        evidence_id,
                        "stable_external_identity",
                        candidate,
                    )
                )
                attachments.append(self._attachment(evidence_id, candidate, "reused_stable"))
                if exact is not None:
                    exact_index.setdefault(exact, evidence_id)
                continue

            log_key = self._policy.log_aggregation_key(candidate)
            if log_key is not None and log_key in log_index:
                evidence_id = log_index[log_key]
                aggregate = self._append_aggregate(aggregate_state[evidence_id], candidate)
                aggregate_state[evidence_id] = aggregate
                updated_aggregate_ids.add(evidence_id)
                decisions.append(
                    self._decision(
                        DeduplicationAction.AGGREGATE,
                        evidence_id,
                        "matching_log_signature_service_and_time_bucket",
                        candidate,
                        aggregate.aggregation_key_hash,
                    )
                )
                attachments.append(self._attachment(evidence_id, candidate, "aggregated"))
                if exact is not None:
                    exact_index.setdefault(exact, evidence_id)
                continue

            aggregation_hash = (
                self._policy.aggregation_key_hash(log_key) if log_key is not None else None
            )
            evidence_id = self._ids.create_id(
                candidate, aggregation_key_hash=aggregation_hash
            )
            if evidence_id in known_ids or evidence_id in creates:
                decisions.append(
                    self._decision(
                        DeduplicationAction.REUSE,
                        evidence_id,
                        "matching_deterministic_identity",
                        candidate,
                        aggregation_hash,
                    )
                )
                attachments.append(self._attachment(evidence_id, candidate, "reused_identity"))
                if exact is not None:
                    exact_index.setdefault(exact, evidence_id)
                continue

            known_ids.add(evidence_id)
            creates[evidence_id] = IdentifiedEvidenceCandidate(evidence_id, candidate)
            decisions.append(
                self._decision(
                    DeduplicationAction.CREATE,
                    evidence_id,
                    "new_identity",
                    candidate,
                    aggregation_hash,
                )
            )
            attachments.append(self._attachment(evidence_id, candidate, "created"))
            if exact is not None:
                exact_index[exact] = evidence_id
            if stable is not None:
                stable_index[stable] = evidence_id
            if log_key is not None:
                log_index[log_key] = evidence_id
                aggregate_state[evidence_id] = self._aggregate_from_candidate(
                    evidence_id, candidate, log_key, aggregation_hash
                )

        return DeduplicationResult(
            decisions=tuple(decisions),
            creates=tuple(creates[evidence_id] for evidence_id in sorted(creates)),
            aggregates=tuple(
                aggregate_state[evidence_id]
                for evidence_id in sorted(updated_aggregate_ids)
            ),
            provenance_attachments=self._unique_attachments(attachments),
        )

    def _candidate_sort_key(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str, str, str, str, str]:
        external = self._ids.external_id(candidate) or ""
        exact = self._policy.exact_key(candidate)
        exact_text = "|".join(exact) if exact is not None else ""
        log_key = self._policy.log_aggregation_key(candidate)
        log_text = "|".join(str(item) for item in log_key) if log_key is not None else ""
        return (
            candidate.source_type,
            external,
            log_text,
            exact_text,
            candidate.provenance.batch_id,
            candidate.provenance.query_id,
        )

    def _stable_key_for_candidate(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str] | None:
        external = self._ids.external_id(candidate)
        return None if external is None else (candidate.source_type, external)

    def _stable_key_for_record(self, record: EvidenceRecord) -> tuple[str, str] | None:
        field = {
            "changes": "revision",
            "deployments": "deployment_id",
            "pipelines": "pipeline_run_id",
            "configuration": "configuration_change_id",
        }.get(record.source_type)
        if field is None:
            return None
        attributes = thaw_json(record.attributes)
        if not isinstance(attributes, Mapping):
            return None
        value = attributes.get(field)
        return (
            (record.source_type, value)
            if isinstance(value, str) and value.strip()
            else None
        )

    @staticmethod
    def _exact_key_for_record(record: EvidenceRecord) -> tuple[str, str, str] | None:
        source_record_id = record.provenance.source_record_id
        if source_record_id is None:
            return None
        return (
            record.source_type,
            source_record_id,
            record.provenance.raw_payload_hash,
        )

    def _log_key_for_record(self, record: EvidenceRecord) -> tuple[str, str, int] | None:
        return self._policy.log_aggregation_key_for_record(record)

    def _aggregate_from_candidate(
        self,
        evidence_id: str,
        candidate: SecuredEvidenceCandidate,
        key: tuple[str, str, int],
        aggregation_key_hash: str | None,
    ) -> RepeatedEventAggregate:
        if aggregation_key_hash is None:
            raise ValueError("log aggregation requires LogAggregationPolicy")
        return RepeatedEventAggregate(
            evidence_id=evidence_id,
            aggregation_key_hash=aggregation_key_hash,
            source_type=candidate.source_type,
            service=candidate.service,
            bucket_start=self._policy.bucket_start(key),
            occurrence_count=1,
            first_event_time=candidate.timestamps.event_time,
            last_event_time=candidate.timestamps.event_time,
            contributing_provenance=(candidate.provenance,),
            truncated_source=candidate.quality.truncated_source,
        )

    def _aggregate_from_record(
        self, record: EvidenceRecord, key: tuple[str, str, int]
    ) -> RepeatedEventAggregate:
        attributes = thaw_json(record.attributes)
        count = (
            attributes.get("occurrence_count", 1)
            if isinstance(attributes, Mapping)
            else 1
        )
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            count = 1
        return RepeatedEventAggregate(
            evidence_id=record.evidence_id,
            aggregation_key_hash=self._policy.aggregation_key_hash(key),
            source_type=record.source_type,
            service=record.service or "unknown",
            bucket_start=self._policy.bucket_start(key),
            occurrence_count=count,
            first_event_time=record.event_time,
            last_event_time=record.event_time,
            contributing_provenance=(record.provenance,),
            truncated_source=record.quality.truncated_source,
        )

    @staticmethod
    def _append_aggregate(
        aggregate: RepeatedEventAggregate, candidate: SecuredEvidenceCandidate
    ) -> RepeatedEventAggregate:
        event_time = candidate.timestamps.event_time
        event_times = [
            value
            for value in (aggregate.first_event_time, event_time)
            if value is not None
        ]
        first = min(event_times) if event_times else None
        event_times = [
            value
            for value in (aggregate.last_event_time, event_time)
            if value is not None
        ]
        last = max(event_times) if event_times else None
        contributors = list(aggregate.contributing_provenance)
        if canonical_bytes(candidate.provenance) not in {
            canonical_bytes(item) for item in contributors
        }:
            contributors.append(candidate.provenance)
        contributors.sort(key=lambda item: canonical_bytes(item))
        return replace(
            aggregate,
            occurrence_count=aggregate.occurrence_count + 1,
            first_event_time=first,
            last_event_time=last,
            contributing_provenance=tuple(contributors),
            truncated_source=aggregate.truncated_source or candidate.quality.truncated_source,
        )

    @staticmethod
    def _decision(
        action: DeduplicationAction,
        evidence_id: str,
        reason: str,
        candidate: SecuredEvidenceCandidate,
        aggregation_key_hash: str | None = None,
    ) -> DeduplicationDecision:
        return DeduplicationDecision(
            action=action,
            evidence_id=evidence_id,
            reason=reason,
            provenance=candidate.provenance,
            aggregation_key_hash=aggregation_key_hash,
        )

    @staticmethod
    def _attachment(
        evidence_id: str, candidate: SecuredEvidenceCandidate, reason: str
    ) -> ProvenanceAttachment:
        return ProvenanceAttachment(evidence_id, candidate.provenance, reason)

    @staticmethod
    def _unique_attachments(
        attachments: list[ProvenanceAttachment],
    ) -> tuple[ProvenanceAttachment, ...]:
        unique: dict[tuple[str, bytes], ProvenanceAttachment] = {}
        for attachment in attachments:
            key = (attachment.evidence_id, canonical_bytes(attachment.provenance))
            unique.setdefault(key, attachment)
        return tuple(unique[key] for key in sorted(unique))
