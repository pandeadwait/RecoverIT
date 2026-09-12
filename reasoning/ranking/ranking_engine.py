"""
Deterministic ranking engine.

Combines pure feature calculators with configurable weights to compute
the final evidence score (0-100), assign ranks, derive confidence labels,
and produce canonical RankedHypothesisSet objects.

See WORK_DIVISION.md §8.6, §8.11 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field

from contracts.common import (
    ConfidenceLabel,
    ContractModel,
    InvestigationStatus,
    StopReason,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import (
    BudgetUsage,
    HypothesisSet,
    RankedHypothesis,
    RankedHypothesisSet,
    ScoreBreakdown,
)
from reasoning.ranking.feature_calculators import (
    calculate_change_consistency,
    calculate_contradiction_penalty,
    calculate_independent_source_support,
    calculate_missing_evidence_penalty,
    calculate_prediction_support,
    calculate_specificity,
    calculate_symptom_coverage,
    calculate_temporal_consistency,
)

logger = logging.getLogger(__name__)


class RankingWeights(ContractModel):
    """
    Configurable weights for hypothesis evidence scoring features.
    Total positive weights sum to 100. Penalties subtract from score.
    """
    independent_source_support: float = Field(
        default=25.0,
        ge=0.0,
        description="Weight for distinct supporting sources (max points).",
    )
    symptom_coverage: float = Field(
        default=20.0,
        ge=0.0,
        description="Weight for fraction of symptoms explained (max points).",
    )
    temporal_consistency: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for timeline causal alignment (max points).",
    )
    change_consistency: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for change/deployment plausibility (max points).",
    )
    specificity: float = Field(
        default=10.0,
        ge=0.0,
        description="Weight for hypothesis testability and specificity (max points).",
    )
    prediction_support: float = Field(
        default=15.0,
        ge=0.0,
        description="Weight for confirmed testable prediction (max points).",
    )
    contradiction_penalty: float = Field(
        default=25.0,
        ge=0.0,
        description="Max penalty subtracted for contradicting evidence.",
    )
    missing_evidence_penalty: float = Field(
        default=15.0,
        ge=0.0,
        description="Max penalty subtracted for unresolved information needs.",
    )


class ConfidenceThresholds(ContractModel):
    """Configurable score boundaries for ConfidenceLabel assignment."""
    high_threshold: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Score required for high confidence.",
    )
    medium_threshold: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="Score required for medium confidence.",
    )


class RankingEngine:
    """
    Deterministic ranking engine for incident hypotheses.

    Guarantees:
    - Ranking is 100% deterministic for identical inputs.
    - Pure feature calculators compute feature values.
    - Configurable weights and thresholds loaded from config or dataclass.
    - Stable tie-breaking using lexicographical hypothesis_id order.
    - Assigned ranks are strictly sequential: 1, 2, 3...
    - Produces canonical RankedHypothesisSet.
    - Inconclusive results produced when evidence is inadequate or no supporting citations exist.
    """

    def __init__(
        self,
        weights: RankingWeights | None = None,
        thresholds: ConfidenceThresholds | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.weights = weights or RankingWeights()
        self.thresholds = thresholds or ConfidenceThresholds()

        if config_path is not None:
            self.load_config(config_path)

    def load_config(self, config_path: str | Path) -> None:
        """Load weights and thresholds from a JSON configuration file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Ranking configuration file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if "weights" in data:
            self.weights = RankingWeights.model_validate(data["weights"])
        if "thresholds" in data:
            self.thresholds = ConfidenceThresholds.model_validate(data["thresholds"])

    def rank(
        self,
        hypothesis_set: HypothesisSet,
        context: IncidentContextSnapshot,
        budget_usage: BudgetUsage | None = None,
        remaining_uncertainty: list[str] | None = None,
    ) -> RankedHypothesisSet:
        """
        Rank a set of hypotheses deterministically against current context.
        """
        ranking_id = f"rank_{hypothesis_set.incident_id}_{int(datetime.now(timezone.utc).timestamp())}"
        now = datetime.now(timezone.utc)
        b_usage = budget_usage or BudgetUsage()

        # Check inconclusive condition: empty hypotheses or no supporting evidence
        has_any_supporting_evidence = any(
            bool(h.supporting_evidence) for h in hypothesis_set.hypotheses
        )

        if not hypothesis_set.hypotheses or not has_any_supporting_evidence:
            return RankedHypothesisSet(
                incident_id=hypothesis_set.incident_id,
                context_snapshot_id=context.snapshot_id,
                ranking_id=ranking_id,
                created_at=now,
                status=InvestigationStatus.INCONCLUSIVE,
                hypotheses=[],
                remaining_uncertainty=remaining_uncertainty or ["Insufficient supporting evidence available."],
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                budget_usage=b_usage,
            )

        scored_hypotheses: list[tuple[float, str, RankedHypothesis]] = []

        for h in hypothesis_set.hypotheses:
            # 1. Feature calculations
            f_supp = calculate_independent_source_support(h, context)
            f_cov = calculate_symptom_coverage(h, context)
            f_temp = calculate_temporal_consistency(h, context)
            f_chg = calculate_change_consistency(h, context)
            f_spec = calculate_specificity(h, context)
            f_pred = calculate_prediction_support(h, context)
            f_contra_pen = calculate_contradiction_penalty(h, context)
            f_miss_pen = calculate_missing_evidence_penalty(h, context)

            # 2. Weighted component scores
            score_supp = f_supp * self.weights.independent_source_support
            score_cov = f_cov * self.weights.symptom_coverage
            score_temp = f_temp * self.weights.temporal_consistency
            score_chg = f_chg * self.weights.change_consistency
            score_spec = f_spec * self.weights.specificity
            score_pred = f_pred * self.weights.prediction_support
            pen_contra = f_contra_pen * self.weights.contradiction_penalty
            pen_miss = f_miss_pen * self.weights.missing_evidence_penalty

            raw_total = (
                score_supp
                + score_cov
                + score_temp
                + score_chg
                + score_spec
                + score_pred
                - pen_contra
                - pen_miss
            )

            evidence_score = max(0.0, min(100.0, round(raw_total, 2)))
            confidence_label = self._derive_confidence_label(evidence_score)

            breakdown = ScoreBreakdown(
                independent_source_support=round(score_supp, 2),
                symptom_coverage=round(score_cov, 2),
                temporal_consistency=round(score_temp, 2),
                change_consistency=round(score_chg, 2),
                specificity=round(score_spec, 2),
                prediction_support=round(score_pred, 2),
                contradiction_penalty=round(pen_contra, 2),
                missing_evidence_penalty=round(pen_miss, 2),
            )

            ranked_item = RankedHypothesis(
                rank=1,  # reassigned below after sorting
                hypothesis_id=h.hypothesis_id,
                statement=h.statement,
                root_cause_category=h.root_cause_category,
                affected_component=h.affected_component,
                evidence_score=evidence_score,
                confidence_label=confidence_label,
                supporting_evidence=h.supporting_evidence,
                contradicting_evidence=h.contradicting_evidence,
                unresolved_questions=h.missing_information_ids,
                score_breakdown=breakdown,
            )

            # Tuple for sorting: (-score, hypothesis_id) -> highest score first, tiebreak lexicographically
            scored_hypotheses.append((-evidence_score, h.hypothesis_id, ranked_item))

        # Sort deterministically
        scored_hypotheses.sort(key=lambda item: (item[0], item[1]))

        final_ranked_hypotheses: list[RankedHypothesis] = []
        for idx, (_, _, item) in enumerate(scored_hypotheses, start=1):
            final_ranked_hypotheses.append(
                RankedHypothesis(
                    rank=idx,
                    hypothesis_id=item.hypothesis_id,
                    statement=item.statement,
                    root_cause_category=item.root_cause_category,
                    affected_component=item.affected_component,
                    evidence_score=item.evidence_score,
                    confidence_label=item.confidence_label,
                    supporting_evidence=item.supporting_evidence,
                    contradicting_evidence=item.contradicting_evidence,
                    unresolved_questions=item.unresolved_questions,
                    score_breakdown=item.score_breakdown,
                )
            )

        return RankedHypothesisSet(
            incident_id=hypothesis_set.incident_id,
            context_snapshot_id=context.snapshot_id,
            ranking_id=ranking_id,
            created_at=now,
            status=InvestigationStatus.COMPLETED,
            hypotheses=final_ranked_hypotheses,
            remaining_uncertainty=remaining_uncertainty or [],
            stop_reason=None,
            budget_usage=b_usage,
        )

    def _derive_confidence_label(self, score: float) -> ConfidenceLabel:
        if score >= self.thresholds.high_threshold:
            return ConfidenceLabel.HIGH
        elif score >= self.thresholds.medium_threshold:
            return ConfidenceLabel.MEDIUM
        return ConfidenceLabel.LOW
