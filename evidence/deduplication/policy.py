"""Configurable, non-revealing matching and log aggregation rules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from contracts.common import canonical_bytes, thaw_json
from contracts.evidence import EvidenceRecord
from contracts.primitives import PayloadHasher, Sha256PayloadHasher
from evidence.security.models import SecuredEvidenceCandidate


class DeduplicationPolicy(Protocol):
    def exact_key(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str, str] | None: ...

    def log_aggregation_key(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str, int] | None: ...

    def log_aggregation_key_for_record(
        self, record: EvidenceRecord
    ) -> tuple[str, str, int] | None: ...

    def aggregation_key_hash(self, key: tuple[str, str, int]) -> str: ...

    def bucket_start(self, key: tuple[str, str, int]) -> datetime: ...


class LogAggregationPolicy(DeduplicationPolicy):
    """Aggregates only log observations with equal safe signature and UTC bucket."""

    def __init__(
        self,
        *,
        bucket_seconds: int = 300,
        hasher: PayloadHasher | None = None,
    ) -> None:
        if (
            isinstance(bucket_seconds, bool)
            or not isinstance(bucket_seconds, int)
            or bucket_seconds < 1
        ):
            raise ValueError("bucket_seconds must be a positive integer")
        self.bucket_seconds = bucket_seconds
        self._hasher = hasher or Sha256PayloadHasher()

    def exact_key(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str, str] | None:
        source_record_id = candidate.provenance.source_record_id
        if source_record_id is None:
            return None
        return (
            candidate.source_type,
            source_record_id,
            candidate.provenance.raw_payload_hash,
        )

    def log_aggregation_key(
        self, candidate: SecuredEvidenceCandidate
    ) -> tuple[str, str, int] | None:
        event_time = candidate.timestamps.event_time
        if candidate.source_type != "logs" or event_time is None:
            return None
        signature = self._signature(candidate)
        bucket = int(event_time.astimezone(timezone.utc).timestamp()) // self.bucket_seconds
        return (candidate.service, signature, bucket)

    def log_aggregation_key_for_record(
        self, record: EvidenceRecord
    ) -> tuple[str, str, int] | None:
        if (
            record.source_type != "logs"
            or record.event_time is None
            or record.service is None
        ):
            return None
        attributes = thaw_json(record.attributes)
        selected: dict[str, Any] = {}
        if isinstance(attributes, Mapping):
            for name in ("level", "error_signature", "exception_type", "logger"):
                if name in attributes:
                    selected[name] = attributes[name]
        material = {
            "evidence_type": record.evidence_type,
            "service": record.service,
            "summary": " ".join(record.summary.split()).casefold(),
            "attributes": selected,
        }
        signature = self._hasher.digest(canonical_bytes(material))
        bucket = int(record.event_time.astimezone(timezone.utc).timestamp()) // self.bucket_seconds
        return (record.service, signature, bucket)

    def aggregation_key_hash(self, key: tuple[str, str, int]) -> str:
        return self._hasher.digest(canonical_bytes({"key": key}))

    def bucket_start(self, key: tuple[str, str, int]) -> datetime:
        return datetime.fromtimestamp(key[2] * self.bucket_seconds, tz=timezone.utc)

    def _signature(self, candidate: SecuredEvidenceCandidate) -> str:
        attributes = thaw_json(candidate.attributes)
        selected: dict[str, Any] = {}
        if isinstance(attributes, Mapping):
            for name in ("level", "error_signature", "exception_type", "logger"):
                if name in attributes:
                    selected[name] = attributes[name]
        material = {
            "evidence_type": candidate.evidence_type,
            "service": candidate.service,
            "summary": " ".join(candidate.summary.split()).casefold(),
            "attributes": selected,
        }
        return self._hasher.digest(canonical_bytes(material))
