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

from pydantic import AliasChoices, Field, model_validator

from contracts.common import (
    ContractModel,
    InformationValueLevel,
    SourceStatus,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import StructuredError


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
    adapter_name: str = Field(
        ...,
        description="Stable identifier of the configured source adapter.",
    )
    unavailable_reason: str | None = Field(
        default=None,
        description="Sanitized explanation when the source is unavailable.",
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

    @model_validator(mode="after")
    def require_unique_source_types(self) -> "SourceCapabilityCatalog":
        source_types = [source.source_type for source in self.sources]
        if len(source_types) != len(set(source_types)):
            raise ValueError("sources must contain at most one capability per source type")
        return self


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
    started_at: datetime = Field(
        ...,
        description="When execution of this query started.",
    )
    completed_at: datetime = Field(
        ...,
        description="When execution of this query completed.",
    )

    @model_validator(mode="after")
    def require_valid_execution_window(self) -> "SourceResult":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be at or after started_at")
        return self


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
    errors: list[StructuredError] = Field(
        default_factory=list,
        description="Structured errors from failed queries.",
    )


# ---------------------------------------------------------------------------
# Evidence Query Plan (reasoning -> collection)
# ---------------------------------------------------------------------------


class EvidenceQuery(ContractModel):
    """One validated, read-only question for a source adapter."""

    query_id: str = Field(..., description="Unique identifier for this query.")
    source_type: SourceType = Field(..., description="Source category to query.")
    question: str = Field(..., description="Information the query should answer.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters validated against the source capability.",
    )
    related_information_ids: list[str] = Field(default_factory=list)
    discriminates_hypothesis_ids: list[str] = Field(default_factory=list)
    expected_information_value: InformationValueLevel = Field(
        default=InformationValueLevel.MEDIUM
    )


class EvidenceQueryPlan(ContractModel):
    """Queries selected for one bounded investigation round."""

    incident_id: str
    plan_id: str
    round_number: int = Field(
        ...,
        ge=1,
        validation_alias=AliasChoices("round_number", "round"),
    )
    queries: list[EvidenceQuery] = Field(default_factory=list)
    stop_reason: StopReason | None = None

    @property
    def round(self) -> int:
        """Compatibility accessor for the pre-migration field name."""

        return self.round_number

    @model_validator(mode="after")
    def require_queries_or_stop_reason(self) -> "EvidenceQueryPlan":
        if not self.queries and self.stop_reason is None:
            raise ValueError("an empty query plan requires stop_reason")
        if self.queries and self.stop_reason is not None:
            raise ValueError("stop_reason requires an empty query list")
        return self
