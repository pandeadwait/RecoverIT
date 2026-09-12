"""
Collection contracts.

Defines SourceCapabilityCatalog (Person 1 → Person 3) and
RawEvidenceBatch (Person 1 → Person 2).

Person 3 consumes SourceCapabilityCatalog to plan queries.
Person 3 produces EvidenceQueryPlan (defined in contracts.investigation).

See WORK_DIVISION.md §6.4 and §6.5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from contracts.common import ContractModel, SourceStatus, SourceType


# ---------------------------------------------------------------------------
# Source Capability Catalog (Person 1 → Person 3)
# ---------------------------------------------------------------------------


class SourceCapability(ContractModel):
    """Describes what a single data source can provide."""
    source_type: SourceType = Field(
        ...,
        description="Category of data this source provides.",
    )
    available: bool = Field(
        ...,
        description="Whether the source is currently reachable.",
    )
    supported_query_fields: list[str] = Field(
        default_factory=list,
        description="Query parameter names this source accepts.",
    )
    maximum_window_seconds: int = Field(
        default=86400,
        description="Maximum time window for a single query in seconds.",
    )
    maximum_items: int = Field(
        default=1000,
        description="Maximum number of items returned in a single query.",
    )


class SourceCapabilityCatalog(ContractModel):
    """
    Describes all available data sources for an incident.

    Produced by Person 1; consumed by Person 3 to plan evidence queries.

    See WORK_DIVISION.md §6.5.
    """
    incident_id: str = Field(
        ...,
        description="Incident these capabilities apply to.",
    )
    generated_at: datetime = Field(
        ...,
        description="When this catalog was generated.",
    )
    sources: list[SourceCapability] = Field(
        default_factory=list,
        description="Available data sources and their capabilities.",
    )


# ---------------------------------------------------------------------------
# Raw Evidence Batch (Person 1 → Person 2)
# ---------------------------------------------------------------------------


class RawRecord(ContractModel):
    """A single unprocessed record from a data source."""
    source_record_id: str = Field(
        ...,
        description="Original record identifier from the source.",
    )
    event_time: datetime | None = Field(
        default=None,
        description="When the operational event occurred.",
    )
    observed_at: datetime | None = Field(
        default=None,
        description="When the source observed or recorded the event.",
    )
    content_type: str = Field(
        ...,
        description="Classification of the raw content.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific data. Treated as untrusted.",
    )


class SourceResult(ContractModel):
    """Result of executing a single query against a data source."""
    query_id: str = Field(
        ...,
        description="Matches the query_id in the EvidenceQueryPlan.",
    )
    source_type: SourceType = Field(
        ...,
        description="Source category that was queried.",
    )
    source_adapter: str = Field(
        ...,
        description="Identifier of the adapter that executed the query.",
    )
    source_status: SourceStatus = Field(
        ...,
        description="Outcome of the source query.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the result set was truncated.",
    )
    records: list[RawRecord] = Field(
        default_factory=list,
        description="Raw records returned by the source.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered during collection.",
    )


class RawEvidenceBatch(ContractModel):
    """
    Complete collection result for one evidence query plan.

    Produced by Person 1's CollectionService; consumed by Person 2
    for normalization.

    See WORK_DIVISION.md §6.5.
    """
    incident_id: str = Field(
        ...,
        description="Incident this batch belongs to.",
    )
    plan_id: str = Field(
        ...,
        description="The EvidenceQueryPlan that triggered this collection.",
    )
    batch_id: str = Field(
        ...,
        description="Unique identifier for this batch.",
    )
    collected_at: datetime = Field(
        ...,
        description="When collection completed.",
    )
    results: list[SourceResult] = Field(
        default_factory=list,
        description="Per-query results.",
    )
    errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured errors from failed queries.",
    )
