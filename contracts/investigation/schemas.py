"""
Investigation contracts.

Defines InvestigationBudget, MissingInformationAssessment, and
EvidenceQueryPlan. Person 3 owns these schemas.

See WORK_DIVISION.md §8.4 and §8.5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, Field, model_validator

from contracts.common import (
    ContractModel,
    InformationGapCategory,
    InformationPriority,
    InformationValueLevel,
    SourceType,
    StopReason,
)


# ---------------------------------------------------------------------------
# Investigation Budget (configuration → Person 3)
# ---------------------------------------------------------------------------


class InvestigationBudget(ContractModel):
    """
    Configurable limits for an investigation run.

    Input and output units are provider-neutral accounting values.
    A model adapter may map them to tokens or another billing unit.

    See WORK_DIVISION.md §8.4.
    """
    max_rounds: int = Field(
        default=6,
        ge=1,
        description="Maximum evidence-gathering rounds.",
    )
    max_queries: int = Field(
        default=12,
        ge=1,
        description="Maximum total evidence queries.",
    )
    max_elapsed_seconds: int = Field(
        default=900,
        ge=1,
        description="Maximum wall-clock investigation time in seconds.",
    )
    max_reasoning_calls: int = Field(
        default=10,
        ge=1,
        description="Maximum calls to the reasoning provider.",
    )
    max_input_units: int = Field(
        default=100_000,
        ge=1,
        description="Maximum provider-neutral input units (e.g. tokens).",
    )
    max_output_units: int = Field(
        default=20_000,
        ge=1,
        description="Maximum provider-neutral output units (e.g. tokens).",
    )
    minimum_hypotheses: int = Field(
        default=2,
        ge=1,
        description="Minimum hypotheses to generate when evidence permits.",
    )
    maximum_hypotheses: int = Field(
        default=5,
        ge=1,
        description="Maximum hypotheses to generate.",
    )


# ---------------------------------------------------------------------------
# Missing Information Assessment (Person 3 intermediate output)
# ---------------------------------------------------------------------------


class KnownFact(ContractModel):
    """A fact established from existing evidence."""
    statement: str = Field(
        ...,
        description="Human-readable statement of the known fact.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_ids", "supporting_evidence_ids"),
        description="Evidence records supporting this fact.",
    )


class MissingInformationItem(ContractModel):
    """A specific information gap that should be addressed."""
    information_id: str = Field(
        ...,
        description="Unique identifier for this information need.",
    )
    question: str = Field(
        ...,
        description="What we need to find out.",
    )
    reason: str = Field(
        ...,
        description="Why this information matters for the investigation.",
    )
    priority: InformationPriority = Field(
        ...,
        description="How important it is to resolve this gap.",
    )
    candidate_sources: list[SourceType] = Field(
        default_factory=list,
        description="Source types that could answer this question.",
    )
    category: InformationGapCategory | str = Field(
        default=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
        description="Classification of this information gap.",
    )
    resolved: bool = Field(
        default=False,
        description="Whether this gap has been filled.",
    )


class MissingInformationAssessment(ContractModel):
    """
    Analysis of what is known, what is missing, and what is unavailable.

    Produced by Person 3's assessor; used internally to drive query planning.

    See WORK_DIVISION.md §8.5.
    """
    incident_id: str = Field(..., description="Parent incident.")
    assessment_id: str = Field(
        ...,
        description="Unique identifier for this assessment.",
    )
    generated_at: datetime | None = Field(
        default=None,
        description="When the assessment was produced, when supplied by the provider.",
    )
    known_facts: list[KnownFact] = Field(
        default_factory=list,
        description="Facts established from current evidence.",
    )
    missing_information: list[MissingInformationItem] = Field(
        default_factory=list,
        description="Information gaps that should be investigated.",
    )
    unavailable_information: list[MissingInformationItem] = Field(
        default_factory=list,
        description="Information that cannot be obtained (sources unavailable).",
    )
    recommended_stop: bool = Field(
        default=False,
        description="Whether the assessor recommends stopping the investigation.",
    )


# ---------------------------------------------------------------------------
# Evidence Query Plan (Person 3 → Person 1)
# ---------------------------------------------------------------------------


class EvidenceQueryPlanQuery(ContractModel):
    """A single query to execute against a data source."""
    query_id: str = Field(
        ...,
        description="Unique identifier for this query.",
    )
    source_type: SourceType = Field(
        ...,
        description="Which source category to query.",
    )
    question: str = Field(
        ...,
        description="Human-readable description of what this query seeks.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-neutral typed query parameters.",
    )
    related_information_ids: list[str] = Field(
        default_factory=list,
        description="MissingInformationItem IDs this query addresses.",
    )
    discriminates_hypothesis_ids: list[str] = Field(
        default_factory=list,
        description="Hypothesis IDs this query could strengthen or weaken.",
    )
    expected_information_value: InformationValueLevel = Field(
        default=InformationValueLevel.MEDIUM,
        description="Expected value of the information this query provides.",
    )


class EvidenceQueryPlan(ContractModel):
    """
    A set of queries to collect evidence for the current investigation round.

    Produced by Person 3; consumed by Person 1's CollectionService.
    The plan can contain zero queries only when stop_reason is present.

    See WORK_DIVISION.md §8.5.
    """
    incident_id: str = Field(..., description="Parent incident.")
    plan_id: str = Field(
        ...,
        description="Unique identifier for this plan.",
    )
    round: int = Field(
        ...,
        ge=1,
        description="Which investigation round this plan belongs to.",
    )
    queries: list[EvidenceQueryPlanQuery] = Field(
        default_factory=list,
        description="Queries to execute.",
    )
    stop_reason: StopReason | None = Field(
        default=None,
        description="If set, no queries are needed and investigation should stop.",
    )

    @model_validator(mode="after")
    def require_queries_or_stop_reason(self) -> "EvidenceQueryPlan":
        if not self.queries and self.stop_reason is None:
            raise ValueError("an empty query plan requires stop_reason")
        if self.queries and self.stop_reason is not None:
            raise ValueError("stop_reason requires an empty query list")
        return self
