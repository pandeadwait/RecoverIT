"""Versioned, technology-neutral contracts for the RecoverIT investigation flow.

The three workstreams keep their implementation-specific helpers in submodules;
this package exposes the canonical integration-boundary contracts.
"""

from contracts.collection import (
    EvidenceQuery,
    EvidenceQueryPlan,
    RawEvidenceBatch,
    RawEvidenceRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.context import IncidentContextSnapshot, IncidentSummary
from contracts.errors import ContractValidationError, ProcessingError, ProcessingWarning
from contracts.evidence import EvidenceFilter, EvidenceProvenance, EvidenceQuality, EvidenceRecord
from contracts.incident import IncidentAlert, IncidentSeed
from contracts.timeline import TemporalRelationship, Timeline, TimelineEvent

__all__ = [
    "ContractValidationError",
    "EvidenceQuery",
    "EvidenceQueryPlan",
    "EvidenceFilter",
    "EvidenceProvenance",
    "EvidenceQuality",
    "EvidenceRecord",
    "IncidentAlert",
    "IncidentContextSnapshot",
    "IncidentSeed",
    "IncidentSummary",
    "ProcessingError",
    "ProcessingWarning",
    "RawEvidenceBatch",
    "RawEvidenceRecord",
    "SourceCapability",
    "SourceCapabilityCatalog",
    "SourceResult",
    "TemporalRelationship",
    "Timeline",
    "TimelineEvent",
]
