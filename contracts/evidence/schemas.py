"""
Evidence contracts.

Defines EvidenceRecord, TimelineEvent, TemporalRelationship, and
IncidentContextSnapshot. Person 2 owns implementation; Person 3
consumes IncidentContextSnapshot and reads EvidenceRecords by ID.

See WORK_DIVISION.md §7.4 and §7.5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from contracts.common import (
    ContractModel,
    EvidenceType,
    Reliability,
    RelationshipCreator,
    RelationshipType,
    SourceCoverageStatus,
    SourceType,
    Severity,
    TimelineCategory,
)


# ---------------------------------------------------------------------------
# Evidence Record (Person 2 owns, Person 3 reads)
# ---------------------------------------------------------------------------


class EvidenceProvenance(ContractModel):
    """Tracks where a piece of evidence originated."""
    batch_id: str = Field(..., description="RawEvidenceBatch that contained this record.")
    query_id: str = Field(..., description="Query that retrieved this record.")
    source_record_id: str = Field(..., description="Original ID from the source system.")
    source_adapter: str = Field(..., description="Adapter that collected the record.")
    raw_payload_hash: str = Field(..., description="SHA-256 hash of the original payload.")


class EvidenceQuality(ContractModel):
    """Quality metadata for an evidence record."""
    reliability: Reliability = Field(..., description="Trustworthiness assessment.")
    freshness_seconds: int = Field(
        default=0,
        description="Seconds between event time and collection time.",
    )
    truncated_source: bool = Field(
        default=False,
        description="Whether the source data was truncated.",
    )
    redactions_applied: bool = Field(
        default=False,
        description="Whether any redactions were applied.",
    )


class EvidenceRecord(ContractModel):
    """
    Canonical normalized evidence record.

    Produced by Person 2's normalization pipeline; consumed by Person 3
    for hypothesis generation and citation.

    See WORK_DIVISION.md §7.5.
    """
    evidence_id: str = Field(..., description="Globally unique evidence identifier.")
    incident_id: str = Field(..., description="Incident this evidence belongs to.")
    source_type: SourceType = Field(..., description="Category of data source.")
    evidence_type: EvidenceType = Field(..., description="Classification of the evidence.")
    service: str = Field(..., description="Canonical service identifier.")
    event_time: datetime | None = Field(
        default=None,
        description="When the operational event occurred.",
    )
    observed_at: datetime | None = Field(
        default=None,
        description="When the source observed the event.",
    )
    collected_at: datetime = Field(
        ...,
        description="When this system collected the evidence.",
    )
    summary: str = Field(
        ...,
        description="Human-readable summary of the evidence.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured attributes extracted from the evidence.",
    )
    provenance: EvidenceProvenance = Field(
        ...,
        description="Traceability to the raw source record.",
    )
    quality: EvidenceQuality = Field(
        ...,
        description="Quality and reliability metadata.",
    )


# ---------------------------------------------------------------------------
# Timeline (Person 2 owns, Person 3 reads)
# ---------------------------------------------------------------------------


class TimelineEvent(ContractModel):
    """
    A single event in the chronological incident timeline.

    See WORK_DIVISION.md §7.5.
    """
    timeline_event_id: str = Field(
        ...,
        description="Unique identifier for this timeline event.",
    )
    incident_id: str = Field(..., description="Parent incident.")
    event_time: datetime = Field(..., description="When the event occurred.")
    time_uncertainty_ms: int = Field(
        default=0,
        description="Uncertainty in the event time in milliseconds.",
    )
    category: TimelineCategory = Field(
        ...,
        description="Type of timeline event.",
    )
    title: str = Field(..., description="Short human-readable description.")
    service: str = Field(..., description="Affected service.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence records backing this timeline event.",
    )


class TemporalRelationship(ContractModel):
    """
    A typed relationship between two timeline events.

    See WORK_DIVISION.md §7.5.
    """
    relationship_id: str = Field(
        ...,
        description="Unique identifier for this relationship.",
    )
    incident_id: str = Field(..., description="Parent incident.")
    from_event_id: str = Field(
        ...,
        description="Source timeline event.",
    )
    to_event_id: str = Field(
        ...,
        description="Target timeline event.",
    )
    relationship_type: RelationshipType = Field(
        ...,
        description="Nature of the relationship.",
    )
    delta_ms: int | None = Field(
        default=None,
        description="Time delta between events in milliseconds.",
    )
    created_by: RelationshipCreator = Field(
        default=RelationshipCreator.DETERMINISTIC,
        description="How this relationship was established.",
    )


# ---------------------------------------------------------------------------
# Context Snapshot (Person 2 → Person 3)
# ---------------------------------------------------------------------------


class EvidenceSummaryProjection(ContractModel):
    """Compact evidence projection included in context snapshots."""
    evidence_id: str = Field(..., description="References a full EvidenceRecord.")
    source_type: SourceType = Field(..., description="Source category.")
    evidence_type: EvidenceType = Field(..., description="Evidence classification.")
    event_time: datetime | None = Field(default=None, description="When the event occurred.")
    summary: str = Field(..., description="Human-readable summary.")
    quality: EvidenceQuality = Field(..., description="Quality metadata.")


class IncidentSummary(ContractModel):
    """Compact incident identity within a context snapshot."""
    service: str = Field(..., description="Affected service.")
    environment: str = Field(..., description="Environment.")
    severity: Severity = Field(..., description="Severity level.")
    detected_at: datetime = Field(..., description="Detection time.")
    summary: str = Field(..., description="Incident summary.")


class TimelineEventProjection(ContractModel):
    """Compact timeline event within a context snapshot."""
    timeline_event_id: str = Field(..., description="Timeline event ID.")
    event_time: datetime = Field(..., description="When the event occurred.")
    category: TimelineCategory = Field(..., description="Event category.")
    title: str = Field(..., description="Short description.")
    evidence_ids: list[str] = Field(default_factory=list, description="Supporting evidence.")


class IncidentContextSnapshot(ContractModel):
    """
    Complete investigative context at a point in time.

    Assembled by Person 2; consumed by Person 3 for hypothesis
    generation, revision, and ranking.

    See WORK_DIVISION.md §7.5.
    """
    snapshot_id: str = Field(..., description="Unique snapshot identifier.")
    incident_id: str = Field(..., description="Parent incident.")
    revision: int = Field(..., description="Monotonically increasing revision number.")
    created_at: datetime = Field(..., description="When this snapshot was assembled.")
    incident: IncidentSummary = Field(
        ...,
        description="Compact incident identity.",
    )
    evidence: list[EvidenceSummaryProjection] = Field(
        default_factory=list,
        description="Compact evidence projections.",
    )
    timeline: list[TimelineEventProjection] = Field(
        default_factory=list,
        description="Chronological timeline events.",
    )
    relationships: list[TemporalRelationship] = Field(
        default_factory=list,
        description="Temporal relationships between events.",
    )
    source_coverage: dict[str, SourceCoverageStatus] = Field(
        default_factory=dict,
        description="Query status per source type.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues in context assembly.",
    )
