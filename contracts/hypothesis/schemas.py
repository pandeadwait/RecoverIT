"""
Hypothesis contracts.

Defines Hypothesis, HypothesisSet, and RankedHypothesisSet.
Person 3 owns these schemas. RankedHypothesisSet is the final
system output — the project boundary.

See WORK_DIVISION.md §8.5 and §8.6.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from contracts.common import (
    ConfidenceLabel,
    ContractModel,
    HypothesisStatus,
    InvestigationStatus,
    RootCauseCategory,
    StopReason,
)


# ---------------------------------------------------------------------------
# Evidence Citation (used within hypotheses)
# ---------------------------------------------------------------------------


class EvidenceCitation(ContractModel):
    """A reference to evidence that supports or contradicts a hypothesis."""
    evidence_id: str = Field(
        ...,
        description="References a stored EvidenceRecord.",
    )
    reason: str = Field(
        ...,
        description="How this evidence affects the hypothesis.",
    )


# ---------------------------------------------------------------------------
# Hypothesis (Person 3 intermediate output)
# ---------------------------------------------------------------------------


class Hypothesis(ContractModel):
    """
    A single root-cause hypothesis with linked evidence.

    See WORK_DIVISION.md §8.5.
    """
    hypothesis_id: str = Field(
        ...,
        description="Globally unique hypothesis identifier.",
    )
    incident_id: str = Field(
        ...,
        description="Parent incident.",
    )
    revision: int = Field(
        default=1,
        ge=1,
        description="Revision number, incremented on each update.",
    )
    statement: str = Field(
        ...,
        description="Clear causal statement explaining the root cause.",
    )
    root_cause_category: RootCauseCategory = Field(
        ...,
        description="Broad classification of the root cause.",
    )
    affected_component: str = Field(
        ...,
        description="Service or component affected.",
    )
    supporting_evidence: list[EvidenceCitation] = Field(
        default_factory=list,
        description="Evidence that supports this hypothesis.",
    )
    contradicting_evidence: list[EvidenceCitation] = Field(
        default_factory=list,
        description="Evidence that contradicts this hypothesis.",
    )
    missing_information_ids: list[str] = Field(
        default_factory=list,
        description="Information gaps relevant to this hypothesis.",
    )
    testable_prediction: str = Field(
        default="",
        description="An observation that should exist if this hypothesis is correct.",
    )
    status: HypothesisStatus = Field(
        default=HypothesisStatus.ACTIVE,
        description="Lifecycle status of this hypothesis.",
    )


class HypothesisSet(ContractModel):
    """
    A collection of hypotheses produced during one generation or revision step.
    """
    incident_id: str = Field(
        ...,
        description="Parent incident.",
    )
    hypotheses: list[Hypothesis] = Field(
        default_factory=list,
        description="All hypotheses, including rejected ones.",
    )
    generated_at: datetime = Field(
        ...,
        description="When this set was produced.",
    )


# ---------------------------------------------------------------------------
# Ranked Hypothesis Set (Person 3 final output — project boundary)
# ---------------------------------------------------------------------------


class ScoreBreakdown(ContractModel):
    """Detailed breakdown of the evidence score for a hypothesis."""
    independent_source_support: float = Field(
        default=0.0,
        description="Score from distinct source types in support.",
    )
    symptom_coverage: float = Field(
        default=0.0,
        description="Fraction of symptoms explained.",
    )
    temporal_consistency: float = Field(
        default=0.0,
        description="How well the causal timeline aligns.",
    )
    change_consistency: float = Field(
        default=0.0,
        description="Whether the cited change plausibly causes the failure.",
    )
    specificity: float = Field(
        default=0.0,
        description="How narrow/testable the hypothesis is.",
    )
    prediction_support: float = Field(
        default=0.0,
        description="Whether the testable prediction was confirmed.",
    )
    contradiction_penalty: float = Field(
        default=0.0,
        description="Penalty from contradicting evidence.",
    )
    missing_evidence_penalty: float = Field(
        default=0.0,
        description="Penalty from unresolved critical information.",
    )


class RankedHypothesis(ContractModel):
    """A hypothesis with its rank and evidence score."""
    rank: int = Field(
        ...,
        ge=1,
        description="Unique rank (1 = most likely).",
    )
    hypothesis_id: str = Field(..., description="References the Hypothesis.")
    statement: str = Field(..., description="The causal statement.")
    root_cause_category: RootCauseCategory = Field(
        ...,
        description="Root-cause classification.",
    )
    affected_component: str = Field(
        ...,
        description="Affected service or component.",
    )
    evidence_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Evidence score (0–100). Not a probability.",
    )
    confidence_label: ConfidenceLabel = Field(
        ...,
        description="Human-readable confidence band.",
    )
    supporting_evidence: list[EvidenceCitation] = Field(
        default_factory=list,
        description="Evidence supporting this hypothesis.",
    )
    contradicting_evidence: list[EvidenceCitation] = Field(
        default_factory=list,
        description="Evidence contradicting this hypothesis.",
    )
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="Questions still unanswered for this hypothesis.",
    )
    score_breakdown: ScoreBreakdown = Field(
        ...,
        description="Detailed feature-by-feature breakdown.",
    )


class BudgetUsage(ContractModel):
    """How much of the investigation budget was consumed."""
    rounds: int = Field(default=0, ge=0, description="Rounds used.")
    queries: int = Field(default=0, ge=0, description="Queries executed.")
    reasoning_calls: int = Field(default=0, ge=0, description="Reasoning calls made.")


class RankedHypothesisSet(ContractModel):
    """
    The final ranked output — this is the project boundary.

    Contains ranked hypotheses with evidence scores and breakdowns,
    remaining uncertainty, and budget usage.

    If the investigation could not produce reliable results,
    status is 'inconclusive' with stop_reason explaining why.

    See WORK_DIVISION.md §8.6.
    """
    incident_id: str = Field(..., description="Parent incident.")
    context_snapshot_id: str = Field(
        ...,
        description="The IncidentContextSnapshot used for final ranking.",
    )
    ranking_id: str = Field(
        ...,
        description="Unique identifier for this ranking.",
    )
    created_at: datetime = Field(
        ...,
        description="When the ranking was produced.",
    )
    status: InvestigationStatus = Field(
        ...,
        description="Whether the investigation completed or was inconclusive.",
    )
    hypotheses: list[RankedHypothesis] = Field(
        default_factory=list,
        description="Hypotheses ranked by evidence score (highest first).",
    )
    remaining_uncertainty: list[str] = Field(
        default_factory=list,
        description="Questions that remain unanswered.",
    )
    stop_reason: StopReason | None = Field(
        default=None,
        description="Why the investigation stopped (for inconclusive runs).",
    )
    budget_usage: BudgetUsage = Field(
        ...,
        description="How much of the budget was consumed.",
    )
