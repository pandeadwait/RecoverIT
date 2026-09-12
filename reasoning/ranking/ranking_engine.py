"""
Deterministic ranking engine for root-cause hypotheses.

Computes evidence scores (0–100) using deterministic feature calculators
and configurable weights, applies deterministic tie-breaking, assigns unique ranks,
and produces the final RankedHypothesisSet (the Person 3 project boundary).

See WORK_DIVISION.md §8.6, §8.11 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from contracts.common import (
    ConfidenceLabel,
    ContractModel,
    InvestigationStatus,
    StopReason,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import (
    BudgetUsage,
    Hypothesis,
    HypothesisSet,
    RankedHypothesis,
    RankedHypothesisSet,
    ScoreBreakdown,
)
from reasoning.ranking import feature_calculators as fc

DEFAULT_CONFIG_PATH = Path(__file__).parent / "default_weights.json"


# ---------------------------------------------------------------------------
# Configuration Models
# ---------------------------------------------------------------------------


class RankingWeights(ContractModel):
    """
    Configurable weights for the 8 deterministic ranking features.

    Positive features contribute up to their weight towards evidence_score.
    Penalty features subtract up to their weight from evidence_score.
    """
    independent_source_support: float = Field(
        default=25.0,
        ge=0.0,
        description="Weight for distinct source types in supporting evidence.",
    )
    symptom_coverage: float = Field(
        default=20.0,
        ge=0.0,
        description="Weight for fraction of symptoms explained.",
    )
    temporal_consistency: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for causal timeline alignment.",
    )
    change_consistency: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for cited change plausibility.",
    )
    specificity: float = Field(
        default=10.0,
        ge=0.0,
        description="Weight for hypothesis testability/narrowness.",
    )
    prediction_support: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for confirmed testable prediction.",
    )
    contradiction_penalty: float = Field(
        default=30.0,
        ge=0.0,
        description="Penalty weight for contradicting evidence.",
    )
    missing_evidence_penalty: float = Field(
        default=20.0,
        ge=0.0,
        description="Penalty weight for unresolved critical information.",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankingWeights:
        """Create RankingWeights from a dictionary."""
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: str | Path) -> RankingWeights:
        """Load RankingWeights from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "weights" in data:
            data = data["weights"]
        return cls.from_dict(data)


class ConfidenceThresholds(ContractModel):
    """
    Thresholds for deriving human-readable confidence labels.

    Score >= high -> HIGH
    high > Score >= medium -> MEDIUM
    Score < medium -> LOW
    """
    high: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Score threshold for high confidence.",
    )
    medium: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="Score threshold for medium confidence.",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> ConfidenceThresholds:
        if self.high < self.medium:
            raise ValueError(
                f"high threshold ({self.high}) must be >= medium threshold ({self.medium})"
            )
        return self

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfidenceThresholds:
        """Create ConfidenceThresholds from a dictionary."""
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: str | Path) -> ConfidenceThresholds:
        """Load ConfidenceThresholds from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "thresholds" in data:
            data = data["thresholds"]
        return cls.from_dict(data)


class RankingConfig(ContractModel):
    """Combined configuration for weights and confidence thresholds."""
    weights: RankingWeights = Field(default_factory=RankingWeights)
    thresholds: ConfidenceThresholds = Field(default_factory=ConfidenceThresholds)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RankingConfig:
        """Create RankingConfig from a dictionary."""
        if "weights" in data or "thresholds" in data:
            weights_data = data.get("weights", {})
            thresholds_data = data.get("thresholds", {})
            return cls(
                weights=RankingWeights.from_dict(weights_data) if weights_data else RankingWeights(),
                thresholds=ConfidenceThresholds.from_dict(thresholds_data) if thresholds_data else ConfidenceThresholds(),
            )
        # Flat dict containing only weights
        return cls(weights=RankingWeights.from_dict(data))

    @classmethod
    def from_file(cls, path: str | Path) -> RankingConfig:
        """Load RankingConfig from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def default(cls) -> RankingConfig:
        """Load default configuration from default_weights.json if present."""
        if DEFAULT_CONFIG_PATH.exists():
            return cls.from_file(DEFAULT_CONFIG_PATH)
        return cls()


# ---------------------------------------------------------------------------
# Ranking Engine
# ---------------------------------------------------------------------------


class RankingEngine:
    """
    Deterministic ranking engine for hypothesis sets.

    Guarantees:
    - Pure, deterministic feature calculation without LLM invocation.
    - Configurable weights and thresholds loaded from files or dictionaries.
    - Evidence score computed as sum of weighted positive features minus penalties, clamped to [0.0, 100.0].
    - Deterministic tie-breaking by (-evidence_score, hypothesis_id).
    - Inconclusive handling when no hypotheses have valid supporting evidence.
    """

    def __init__(
        self,
        weights: RankingWeights | None = None,
        thresholds: ConfidenceThresholds | None = None,
        config: RankingConfig | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        if config_path is not None:
            self._config = RankingConfig.from_file(config_path)
        elif config is not None:
            self._config = config
        else:
            base = RankingConfig.default()
            self._config = RankingConfig(
                weights=weights if weights is not None else base.weights,
                thresholds=thresholds if thresholds is not None else base.thresholds,
            )

    @property
    def config(self) -> RankingConfig:
        return self._config

    @property
    def weights(self) -> RankingWeights:
        return self._config.weights

    @property
    def thresholds(self) -> ConfidenceThresholds:
        return self._config.thresholds

    def calculate_breakdown(
        self,
        hypothesis: Hypothesis,
        context: IncidentContextSnapshot,
    ) -> ScoreBreakdown:
        """
        Calculate individual feature contributions multiplied by their configured weights.
        """
        w = self.weights

        iss = fc.calculate_independent_source_support(hypothesis, context)
        sc = fc.calculate_symptom_coverage(hypothesis, context)
        tc = fc.calculate_temporal_consistency(hypothesis, context)
        cc = fc.calculate_change_consistency(hypothesis, context)
        spec = fc.calculate_specificity(hypothesis, context)
        ps = fc.calculate_prediction_support(hypothesis, context)
        cp = fc.calculate_contradiction_penalty(hypothesis, context)
        mep = fc.calculate_missing_evidence_penalty(hypothesis, context)

        return ScoreBreakdown(
            independent_source_support=round(iss * w.independent_source_support, 2),
            symptom_coverage=round(sc * w.symptom_coverage, 2),
            temporal_consistency=round(tc * w.temporal_consistency, 2),
            change_consistency=round(cc * w.change_consistency, 2),
            specificity=round(spec * w.specificity, 2),
            prediction_support=round(ps * w.prediction_support, 2),
            contradiction_penalty=round(cp * w.contradiction_penalty, 2),
            missing_evidence_penalty=round(mep * w.missing_evidence_penalty, 2),
        )

    def calculate_evidence_score(self, breakdown: ScoreBreakdown) -> float:
        """
        Compute total evidence score from breakdown, clamped to [0.0, 100.0].
        """
        positive_sum = (
            breakdown.independent_source_support
            + breakdown.symptom_coverage
            + breakdown.temporal_consistency
            + breakdown.change_consistency
            + breakdown.specificity
            + breakdown.prediction_support
        )
        penalties = (
            breakdown.contradiction_penalty
            + breakdown.missing_evidence_penalty
        )
        raw_score = positive_sum - penalties
        return round(max(0.0, min(100.0, raw_score)), 2)

    def derive_confidence_label(self, score: float) -> ConfidenceLabel:
        """
        Derive categorical confidence label from numeric evidence score.
        """
        if score >= self.thresholds.high:
            return ConfidenceLabel.HIGH
        if score >= self.thresholds.medium:
            return ConfidenceLabel.MEDIUM
        return ConfidenceLabel.LOW

    def rank(
        self,
        hypothesis_set: HypothesisSet,
        context: IncidentContextSnapshot,
        budget_usage: BudgetUsage | None = None,
        remaining_uncertainty: list[str] | None = None,
        ranking_id: str | None = None,
        stop_reason: StopReason | None = None,
        status: InvestigationStatus | None = None,
        created_at: datetime | None = None,
    ) -> RankedHypothesisSet:
        """
        Rank hypotheses deterministically against incident context.

        If no hypothesis has valid supporting evidence (or hypotheses set is empty),
        returns an inconclusive RankedHypothesisSet with stop_reason set.
        """
        incident_id = hypothesis_set.incident_id or context.incident_id
        effective_created_at = created_at if created_at is not None else context.created_at
        effective_budget_usage = budget_usage or BudgetUsage()

        if ranking_id is None:
            content_key = f"{incident_id}_{context.snapshot_id}_{len(hypothesis_set.hypotheses)}"
            short_hash = hashlib.sha256(content_key.encode()).hexdigest()[:8]
            ranking_id = f"rank_{short_hash}"

        # Determine if inconclusive:
        # Check whether any hypothesis has valid supporting evidence in the context.
        context_evidence_ids = {e.evidence_id for e in context.evidence}
        has_any_valid_support = any(
            any(c.evidence_id in context_evidence_ids for c in h.supporting_evidence)
            for h in hypothesis_set.hypotheses
        )

        is_inconclusive = (
            status == InvestigationStatus.INCONCLUSIVE
            or not hypothesis_set.hypotheses
            or not has_any_valid_support
        )

        if is_inconclusive:
            effective_stop_reason = (
                stop_reason
                if stop_reason is not None
                else StopReason.INSUFFICIENT_EVIDENCE
            )
            effective_uncertainty = (
                remaining_uncertainty
                if remaining_uncertainty is not None
                else ["No hypothesis has valid supporting evidence in the incident context."]
            )
            return RankedHypothesisSet(
                incident_id=incident_id,
                context_snapshot_id=context.snapshot_id,
                ranking_id=ranking_id,
                created_at=effective_created_at,
                status=InvestigationStatus.INCONCLUSIVE,
                hypotheses=[],
                remaining_uncertainty=effective_uncertainty,
                stop_reason=effective_stop_reason,
                budget_usage=effective_budget_usage,
            )

        # Score each hypothesis
        scored_items: list[tuple[float, str, Hypothesis, ScoreBreakdown, ConfidenceLabel]] = []
        for h in hypothesis_set.hypotheses:
            breakdown = self.calculate_breakdown(h, context)
            score = self.calculate_evidence_score(breakdown)
            confidence = self.derive_confidence_label(score)
            scored_items.append((score, h.hypothesis_id, h, breakdown, confidence))

        # Deterministic sorting: highest score first, ties broken by hypothesis_id ascending
        scored_items.sort(key=lambda item: (-item[0], item[1]))

        # Build RankedHypothesis items with 1-based ranks
        ranked_hypotheses: list[RankedHypothesis] = []
        for rank_num, (score, _, h, breakdown, confidence) in enumerate(scored_items, start=1):
            unresolved = [
                f"Unresolved information need: {mid}"
                for mid in h.missing_information_ids
            ]
            ranked_hypotheses.append(
                RankedHypothesis(
                    rank=rank_num,
                    hypothesis_id=h.hypothesis_id,
                    statement=h.statement,
                    root_cause_category=h.root_cause_category,
                    affected_component=h.affected_component,
                    evidence_score=score,
                    confidence_label=confidence,
                    supporting_evidence=list(h.supporting_evidence),
                    contradicting_evidence=list(h.contradicting_evidence),
                    unresolved_questions=unresolved,
                    score_breakdown=breakdown,
                )
            )

        return RankedHypothesisSet(
            incident_id=incident_id,
            context_snapshot_id=context.snapshot_id,
            ranking_id=ranking_id,
            created_at=effective_created_at,
            status=InvestigationStatus.COMPLETED,
            hypotheses=ranked_hypotheses,
            remaining_uncertainty=remaining_uncertainty or [],
            stop_reason=None,
            budget_usage=effective_budget_usage,
        )
