"""Deterministic evidence identity, deduplication, and repeated-event aggregation."""

from evidence.deduplication.identity import DeterministicEvidenceIdStrategy, EvidenceIdStrategy
from evidence.deduplication.models import (
    DeduplicationAction,
    DeduplicationDecision,
    DeduplicationResult,
    IdentifiedEvidenceCandidate,
    ProvenanceAttachment,
    RepeatedEventAggregate,
)
from evidence.deduplication.policy import DeduplicationPolicy, LogAggregationPolicy
from evidence.deduplication.service import EvidenceDeduplicationService

__all__ = [
    "DeduplicationAction",
    "DeduplicationDecision",
    "DeduplicationPolicy",
    "DeduplicationResult",
    "DeterministicEvidenceIdStrategy",
    "EvidenceDeduplicationService",
    "EvidenceIdStrategy",
    "IdentifiedEvidenceCandidate",
    "LogAggregationPolicy",
    "ProvenanceAttachment",
    "RepeatedEventAggregate",
]
