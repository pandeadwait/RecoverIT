"""RawEvidenceBatch — collected raw evidence from source adapters.

Produced by Person 1 after executing an EvidenceQueryPlan.
Consumed by Person 2 for normalization, deduplication, and timeline
construction.  See WORK_DIVISION §6.5.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, Field

from contracts.common import BoundaryModel
from contracts.enums import SourceStatus, SourceType
from contracts.errors import StructuredError


class RawRecord(BoundaryModel):
    """A single raw record from a data source."""

    source_record_id: str
    event_time: AwareDatetime | None = None
    observed_at: AwareDatetime | None = None
    content_type: str
    payload: dict[str, Any]


class QueryResult(BoundaryModel):
    """Result of executing a single query against one source."""

    query_id: str
    source_type: SourceType
    source_adapter: str
    source_status: SourceStatus
    truncated: bool = False
    records: list[RawRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RawEvidenceBatch(BoundaryModel):
    """Batch of raw evidence collected for one plan execution."""

    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    plan_id: str
    batch_id: str
    collected_at: AwareDatetime
    results: list[QueryResult] = Field(default_factory=list)
    errors: list[StructuredError] = Field(default_factory=list)
