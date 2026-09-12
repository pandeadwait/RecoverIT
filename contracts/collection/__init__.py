"""Collection contracts — capabilities, query plans, and evidence batches."""

from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.collection.batch import QueryResult, RawEvidenceBatch, RawRecord

__all__ = [
    "SourceCapability",
    "SourceCapabilityCatalog",
    "EvidenceQuery",
    "EvidenceQueryPlan",
    "QueryResult",
    "RawEvidenceBatch",
    "RawRecord",
]
