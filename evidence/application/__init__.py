"""Evidence ingestion application service and replaceable observability port."""

from evidence.application.models import EvidenceProcessingError, EvidenceProcessingResult
from evidence.application.observability import (
    EvidenceAuditEvent,
    EvidenceAuditSink,
    InMemoryEvidenceAuditSink,
    NoOpEvidenceAuditSink,
)
from evidence.application.service import EvidenceProcessingService

__all__ = [
    "EvidenceAuditEvent",
    "EvidenceAuditSink",
    "EvidenceProcessingError",
    "EvidenceProcessingResult",
    "EvidenceProcessingService",
    "InMemoryEvidenceAuditSink",
    "NoOpEvidenceAuditSink",
]
