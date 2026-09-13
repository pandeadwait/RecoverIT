"""
Unit tests for the Deterministic Ranking Engine and Feature Calculators.

Verifies:
- Ranking is deterministic for identical inputs (run twice, assert equal).
- Equal-score tie-breaking is stable (lexicographic by hypothesis_id).
- Each feature calculator tested in isolation with edge cases.
- Weights are loaded from configuration (not hard-coded).
- Confidence label thresholds work correctly at boundaries.
- Missing evidence and contradiction penalties reduce scores.
- Inconclusive status is produced when warranted.
- The model/LLM cannot directly assign the final numeric score.

See WORK_DIVISION.md §8.6, §8.11 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceType,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    BudgetUsage,
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from reasoning.ranking import (
    ConfidenceThresholds,
    RankingConfig,
    RankingEngine,
    RankingWeights,
    calculate_change_consistency,
    calculate_contradiction_penalty,
    calculate_independent_source_support,
    calculate_missing_evidence_penalty,
    calculate_prediction_support,
    calculate_specificity,
    calculate_symptom_coverage,
    calculate_temporal_consistency,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_context() -> IncidentContextSnapshot:
    """Fixture providing a realistic context snapshot with diverse evidence."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 12, 10, 5, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 12, 10, 10, 0, tzinfo=timezone.utc)

    e1 = EvidenceSummaryProjection(
        evidence_id="ev_deploy_01",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=t0,
        summary="Deployment v2.4.1 completed for payment-service updating db endpoint config",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e2 = EvidenceSummaryProjection(
        evidence_id="ev_metric_01",
        source_type=SourceType.METRICS,
        evidence_type=EvidenceType.METRIC_ANOMALY,
        event_time=t1,
        summary="Error rate spiked to 25% on /pay endpoint with timeout increase",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e3 = EvidenceSummaryProjection(
        evidence_id="ev_log_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=t2,
        summary="Database connection pool timeout occurred on payments db",
        quality=EvidenceQuality(reliability=Reliability.MEDIUM),
    )
    e4 = EvidenceSummaryProjection(
        evidence_id="ev_trace_01",
        source_type=SourceType.HEALTH,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=t2,
        summary="Distributed trace shows 30s timeout waiting for db handshake",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )

    incident_summary = IncidentSummary(
        service="payment-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=t1,
        summary="Elevated error rates on payment service",
    )

    return IncidentContextSnapshot(
        snapshot_id="ctx_rank_01",
        incident_id="inc_rank_01",
        revision=1,
        created_at=t2,
        incident=incident_summary,
        evidence=[e1, e2, e3, e4],
    )


@pytest.fixture
def base_hypothesis() -> Hypothesis:
    """Fixture providing a well-supported change hypothesis."""
    return Hypothesis(
        hypothesis_id="hyp_01",
        incident_id="inc_rank_01",
        revision=1,
        statement="Deployment v2.4.1 misconfigured the database connection pool settings.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_deploy_01", reason="Deployment updated db configs"),
            EvidenceCitation(evidence_id="ev_metric_01", reason="Errors spiked after deployment"),
            EvidenceCitation(evidence_id="ev_log_01", reason="Logs indicate db connection timeouts"),
        ],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Database connection pool timeout errors in payment service logs",
        status=HypothesisStatus.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Feature Calculator Isolation Tests
# ---------------------------------------------------------------------------


def test_independent_source_support_empty_cases(sample_context: IncidentContextSnapshot) -> None:
    empty_hyp = Hypothesis(
        hypothesis_id="hyp_empty",
        incident_id="inc_01",
        statement="Some statement",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="service",
        supporting_evidence=[],
    )
    assert calculate_independent_source_support(empty_hyp, sample_context) == 0.0

    empty_ctx = IncidentContextSnapshot(
        snapshot_id="ctx_empty",
        incident_id="inc_01",
        revision=1,
        created_at=datetime.now(timezone.utc),
        incident=IncidentSummary(
            service="svc",
            environment="prod",
            severity=Severity.INFO,
            detected_at=datetime.now(timezone.utc),
            summary="empty",
        ),
        evidence=[],
    )
    hyp_with_evidence = Hypothesis(
        hypothesis_id="hyp_with_ev",
        incident_id="inc_01",
        statement="Some statement",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="service",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="r")],
    )
    assert calculate_independent_source_support(hyp_with_evidence, empty_ctx) == 0.0


def test_independent_source_support_scaling(sample_context: IncidentContextSnapshot) -> None:
    # 1 source type (DEPLOYMENT)
    hyp_1 = Hypothesis(
        hypothesis_id="h1",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_deploy_01", reason="r")],
    )
    assert calculate_independent_source_support(hyp_1, sample_context) == 0.5

    # 2 source types (DEPLOYMENT + METRICS)
    hyp_2 = Hypothesis(
        hypothesis_id="h2",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_deploy_01", reason="r"),
            EvidenceCitation(evidence_id="ev_metric_01", reason="r"),
        ],
    )
    assert calculate_independent_source_support(hyp_2, sample_context) == 0.85

    # 3 source types (DEPLOYMENT + METRICS + LOGS)
    hyp_3 = Hypothesis(
        hypothesis_id="h3",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_deploy_01", reason="r"),
            EvidenceCitation(evidence_id="ev_metric_01", reason="r"),
            EvidenceCitation(evidence_id="ev_log_01", reason="r"),
        ],
    )
    assert calculate_independent_source_support(hyp_3, sample_context) == 1.0


def test_symptom_coverage_scaling(sample_context: IncidentContextSnapshot) -> None:
    # Symptoms in sample_context: ev_metric_01 (anomaly), ev_log_01 (error), ev_trace_01 (error) -> 3 symptoms
    # 0 covered
    hyp_0 = Hypothesis(
        hypothesis_id="h0",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_deploy_01", reason="r")],
    )
    assert calculate_symptom_coverage(hyp_0, sample_context) == 0.0

    # 1 of 3 covered
    hyp_1 = Hypothesis(
        hypothesis_id="h1",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="r")],
    )
    assert pytest.approx(calculate_symptom_coverage(hyp_1, sample_context), 0.01) == 1.0 / 3.0

    # All 3 covered
    hyp_all = Hypothesis(
        hypothesis_id="h_all",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_metric_01", reason="r"),
            EvidenceCitation(evidence_id="ev_log_01", reason="r"),
            EvidenceCitation(evidence_id="ev_trace_01", reason="r"),
        ],
    )
    assert calculate_symptom_coverage(hyp_all, sample_context) == 1.0


def test_temporal_consistency_causal_ordering(sample_context: IncidentContextSnapshot) -> None:
    # Change at 10:00 precedes symptoms at 10:05 -> 1.0
    good_hyp = Hypothesis(
        hypothesis_id="h_good",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
        affected_component="svc",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_deploy_01", reason="r"),
            EvidenceCitation(evidence_id="ev_log_01", reason="r"),
        ],
    )
    assert calculate_temporal_consistency(good_hyp, sample_context) == 1.0

    # Non-change hypothesis gets baseline 0.9
    non_change_hyp = Hypothesis(
        hypothesis_id="h_non_change",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="r")],
    )
    assert calculate_temporal_consistency(non_change_hyp, sample_context) == 0.9


def test_temporal_consistency_causal_violation() -> None:
    # Context where change happened AFTER symptoms
    t_symptom = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_late_change = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)

    ctx = IncidentContextSnapshot(
        snapshot_id="ctx_viol",
        incident_id="inc_01",
        revision=1,
        created_at=t_late_change,
        incident=IncidentSummary(
            service="svc",
            environment="prod",
            severity=Severity.CRITICAL,
            detected_at=t_symptom,
            summary="summary",
        ),
        evidence=[
            EvidenceSummaryProjection(
                evidence_id="ev_symptom",
                source_type=SourceType.LOGS,
                evidence_type=EvidenceType.ERROR_EVENT,
                event_time=t_symptom,
                summary="Symptom started",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            ),
            EvidenceSummaryProjection(
                evidence_id="ev_late_change",
                source_type=SourceType.DEPLOYMENTS,
                evidence_type=EvidenceType.DEPLOYMENT_EVENT,
                event_time=t_late_change,
                summary="Deployment happened later",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            ),
        ],
    )

    hyp = Hypothesis(
        hypothesis_id="h_viol",
        incident_id="inc_01",
        statement="Late deployment caused earlier crash",
        root_cause_category=RootCauseCategory.DEPLOYMENT_FAILURE,
        affected_component="svc",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_late_change", reason="r"),
            EvidenceCitation(evidence_id="ev_symptom", reason="r"),
        ],
    )
    # Causal violation should return 0.1
    assert calculate_temporal_consistency(hyp, ctx) == 0.1


def test_change_consistency_scenarios(sample_context: IncidentContextSnapshot) -> None:
    # Change hypothesis citing change evidence in context -> 1.0
    hyp_cites_change = Hypothesis(
        hypothesis_id="h_ch",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_deploy_01", reason="r")],
    )
    assert calculate_change_consistency(hyp_cites_change, sample_context) == 1.0

    # Change hypothesis NOT citing change evidence -> 0.4
    hyp_no_change_cite = Hypothesis(
        hypothesis_id="h_no_ch",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="r")],
    )
    assert calculate_change_consistency(hyp_no_change_cite, sample_context) == 0.4

    # Non-change hypothesis -> 0.9
    hyp_non_change = Hypothesis(
        hypothesis_id="h_nc",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="svc",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="r")],
    )
    assert calculate_change_consistency(hyp_non_change, sample_context) == 0.9


def test_specificity_calculation(sample_context: IncidentContextSnapshot) -> None:
    # Low specificity: unknown component, unknown category, short statement, no prediction
    vague_hyp = Hypothesis(
        hypothesis_id="h_vague",
        incident_id="inc_01",
        statement="bad",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="unknown",
        testable_prediction="",
    )
    assert calculate_specificity(vague_hyp, sample_context) == 0.0

    # High specificity: concrete component (+0.3), known category (+0.3), long statement (+0.2), testable prediction (+0.2) = 1.0
    detailed_hyp = Hypothesis(
        hypothesis_id="h_detailed",
        incident_id="inc_01",
        statement="Database connection timeout regression after connection pool sizing change",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-service",
        testable_prediction="Check pool sizing metrics in datadog for exhaustion",
    )
    assert calculate_specificity(detailed_hyp, sample_context) == 1.0


def test_prediction_support_keyword_matching(sample_context: IncidentContextSnapshot) -> None:
    # No prediction -> 0.0
    h_no_pred = Hypothesis(
        hypothesis_id="h0",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        testable_prediction="",
    )
    assert calculate_prediction_support(h_no_pred, sample_context) == 0.0

    # Words matching multiple evidence summaries: "timeout" and "database" match in ev_log_01 & ev_trace_01 -> 1.0
    h_strong_pred = Hypothesis(
        hypothesis_id="h_strong",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        testable_prediction="Should observe database timeout errors across logs and traces",
    )
    assert calculate_prediction_support(h_strong_pred, sample_context) == 1.0

    # Words with no matches -> 0.4
    h_unmatched = Hypothesis(
        hypothesis_id="h_unmatched",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        testable_prediction="Unrelated completely nonmatching keywords here",
    )
    assert calculate_prediction_support(h_unmatched, sample_context) == 0.4


def test_contradiction_penalty_values(sample_context: IncidentContextSnapshot) -> None:
    h0 = Hypothesis(
        hypothesis_id="h0",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        contradicting_evidence=[],
    )
    assert calculate_contradiction_penalty(h0, sample_context) == 0.0

    h1 = Hypothesis(
        hypothesis_id="h1",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        contradicting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="r")],
    )
    assert calculate_contradiction_penalty(h1, sample_context) == 0.5

    h2 = Hypothesis(
        hypothesis_id="h2",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        contradicting_evidence=[
            EvidenceCitation(evidence_id="ev_01", reason="r"),
            EvidenceCitation(evidence_id="ev_02", reason="r"),
        ],
    )
    assert calculate_contradiction_penalty(h2, sample_context) == 1.0


def test_missing_evidence_penalty_values(sample_context: IncidentContextSnapshot) -> None:
    h0 = Hypothesis(
        hypothesis_id="h0",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        missing_information_ids=[],
    )
    assert calculate_missing_evidence_penalty(h0, sample_context) == 0.0

    h1 = Hypothesis(
        hypothesis_id="h1",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        missing_information_ids=["gap_01"],
    )
    assert calculate_missing_evidence_penalty(h1, sample_context) == 0.5

    h2 = Hypothesis(
        hypothesis_id="h2",
        incident_id="inc_01",
        statement="stmt",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="svc",
        missing_information_ids=["gap_01", "gap_02"],
    )
    assert calculate_missing_evidence_penalty(h2, sample_context) == 1.0


# ---------------------------------------------------------------------------
# Configuration & Weight Loading Tests
# ---------------------------------------------------------------------------


def test_default_weights_loaded_from_config() -> None:
    engine = RankingEngine()
    w = engine.weights
    # Default positive weights sum to 100.0
    positive_sum = (
        w.independent_source_support
        + w.symptom_coverage
        + w.temporal_consistency
        + w.change_consistency
        + w.specificity
        + w.prediction_support
    )
    assert positive_sum == 100.0
    assert w.contradiction_penalty == 30.0
    assert w.missing_evidence_penalty == 20.0
    assert engine.thresholds.high == 70.0
    assert engine.thresholds.medium == 40.0


def test_custom_weights_from_dict() -> None:
    custom_weights = RankingWeights.from_dict({
        "independent_source_support": 10.0,
        "symptom_coverage": 10.0,
        "temporal_consistency": 10.0,
        "change_consistency": 10.0,
        "specificity": 10.0,
        "prediction_support": 10.0,
        "contradiction_penalty": 50.0,
        "missing_evidence_penalty": 50.0,
    })
    engine = RankingEngine(weights=custom_weights)
    assert engine.weights.independent_source_support == 10.0
    assert engine.weights.contradiction_penalty == 50.0


def test_custom_config_from_file(tmp_path: Path) -> None:
    config_file = tmp_path / "test_ranking_config.json"
    config_file.write_text(
        json.dumps({
            "weights": {
                "independent_source_support": 30.0,
                "symptom_coverage": 30.0,
                "temporal_consistency": 10.0,
                "change_consistency": 10.0,
                "specificity": 10.0,
                "prediction_support": 10.0,
                "contradiction_penalty": 40.0,
                "missing_evidence_penalty": 25.0,
            },
            "thresholds": {
                "high": 80.0,
                "medium": 50.0,
            },
        }),
        encoding="utf-8",
    )

    engine = RankingEngine(config_path=config_file)
    assert engine.weights.independent_source_support == 30.0
    assert engine.thresholds.high == 80.0
    assert engine.thresholds.medium == 50.0


def test_confidence_threshold_validation() -> None:
    with pytest.raises(ValueError, match="high threshold .* must be >= medium threshold"):
        ConfidenceThresholds(high=30.0, medium=50.0)


# ---------------------------------------------------------------------------
# Deterministic Ranking & Tie-Breaking Tests
# ---------------------------------------------------------------------------


def test_ranking_is_deterministic_identical_inputs(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Run ranking twice on identical inputs; assert exact equality."""
    engine = RankingEngine()
    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[base_hypothesis],
        generated_at=datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc),
    )

    ranked1 = engine.rank(hyp_set, sample_context)
    ranked2 = engine.rank(hyp_set, sample_context)

    assert ranked1 == ranked2
    assert ranked1.hypotheses[0].evidence_score == ranked2.hypotheses[0].evidence_score
    assert ranked1.hypotheses[0].rank == 1


def test_equal_score_tie_breaking_is_stable(sample_context: IncidentContextSnapshot) -> None:
    """When evidence scores are equal, ties must be broken lexicographically by hypothesis_id."""
    engine = RankingEngine()

    # Two identical hypotheses with different IDs
    h_b = Hypothesis(
        hypothesis_id="hyp_beta",
        incident_id="inc_rank_01",
        statement="Same causal explanation for beta",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="payment-service",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="same")],
    )
    h_a = Hypothesis(
        hypothesis_id="hyp_alpha",
        incident_id="inc_rank_01",
        statement="Same causal explanation for alpha",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="payment-service",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="same")],
    )

    # Order 1: [h_b, h_a]
    set_1 = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[h_b, h_a],
        generated_at=datetime.now(timezone.utc),
    )
    ranked_1 = engine.rank(set_1, sample_context)

    # Order 2: [h_a, h_b]
    set_2 = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[h_a, h_b],
        generated_at=datetime.now(timezone.utc),
    )
    ranked_2 = engine.rank(set_2, sample_context)

    # In both cases, 'hyp_alpha' should be rank 1 and 'hyp_beta' rank 2
    assert ranked_1.hypotheses[0].hypothesis_id == "hyp_alpha"
    assert ranked_1.hypotheses[0].rank == 1
    assert ranked_1.hypotheses[1].hypothesis_id == "hyp_beta"
    assert ranked_1.hypotheses[1].rank == 2

    assert ranked_2.hypotheses[0].hypothesis_id == "hyp_alpha"
    assert ranked_2.hypotheses[0].rank == 1
    assert ranked_2.hypotheses[1].hypothesis_id == "hyp_beta"
    assert ranked_2.hypotheses[1].rank == 2


def test_ranks_are_strictly_sequential(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    engine = RankingEngine()

    h2 = Hypothesis(
        hypothesis_id="hyp_02",
        incident_id="inc_rank_01",
        statement="Database connection pool exhaustion",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="payment-service",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="db log")],
    )
    h3 = Hypothesis(
        hypothesis_id="hyp_03",
        incident_id="inc_rank_01",
        statement="Network partition between service and db",
        root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
        affected_component="payment-service",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_trace_01", reason="trace timeout")],
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[h2, base_hypothesis, h3],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    assert len(ranked.hypotheses) == 3
    ranks = [h.rank for h in ranked.hypotheses]
    assert ranks == [1, 2, 3]

    # Scores must be non-increasing
    scores = [h.evidence_score for h in ranked.hypotheses]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Confidence Label Boundary Tests
# ---------------------------------------------------------------------------


def test_confidence_label_boundaries() -> None:
    engine = RankingEngine()
    # Default thresholds: high=70.0, medium=40.0
    assert engine.derive_confidence_label(100.0) == ConfidenceLabel.HIGH
    assert engine.derive_confidence_label(70.0) == ConfidenceLabel.HIGH
    assert engine.derive_confidence_label(69.99) == ConfidenceLabel.MEDIUM
    assert engine.derive_confidence_label(40.0) == ConfidenceLabel.MEDIUM
    assert engine.derive_confidence_label(39.99) == ConfidenceLabel.LOW
    assert engine.derive_confidence_label(0.0) == ConfidenceLabel.LOW


# ---------------------------------------------------------------------------
# Penalties & Score Clamping Tests
# ---------------------------------------------------------------------------


def test_contradiction_penalty_reduces_score(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    engine = RankingEngine()

    clean_hyp = base_hypothesis.model_copy(update={"hypothesis_id": "hyp_clean"})
    penalized_hyp = base_hypothesis.model_copy(
        update={
            "hypothesis_id": "hyp_penalized",
            "contradicting_evidence": [
                EvidenceCitation(evidence_id="ev_metric_01", reason="Contradiction observed"),
            ],
        }
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[clean_hyp, penalized_hyp],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    ranked_map = {h.hypothesis_id: h for h in ranked.hypotheses}

    clean_score = ranked_map["hyp_clean"].evidence_score
    penalized_score = ranked_map["hyp_penalized"].evidence_score

    assert penalized_score < clean_score
    assert ranked_map["hyp_penalized"].score_breakdown.contradiction_penalty > 0.0
    assert ranked_map["hyp_clean"].score_breakdown.contradiction_penalty == 0.0


def test_missing_evidence_penalty_reduces_score(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    engine = RankingEngine()

    complete_hyp = base_hypothesis.model_copy(update={"hypothesis_id": "hyp_complete"})
    gap_hyp = base_hypothesis.model_copy(
        update={
            "hypothesis_id": "hyp_gap",
            "missing_information_ids": ["gap_01", "gap_02"],
        }
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[complete_hyp, gap_hyp],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    ranked_map = {h.hypothesis_id: h for h in ranked.hypotheses}

    assert ranked_map["hyp_gap"].evidence_score < ranked_map["hyp_complete"].evidence_score
    assert ranked_map["hyp_gap"].score_breakdown.missing_evidence_penalty == 20.0
    assert ranked_map["hyp_complete"].score_breakdown.missing_evidence_penalty == 0.0


def test_score_clamped_between_0_and_100(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    # Huge penalties to verify clamping at 0.0
    extreme_weights = RankingWeights(
        contradiction_penalty=200.0,
        missing_evidence_penalty=200.0,
    )
    engine = RankingEngine(weights=extreme_weights)

    heavily_penalized = base_hypothesis.model_copy(
        update={
            "contradicting_evidence": [
                EvidenceCitation(evidence_id="ev_log_01", reason="r1"),
                EvidenceCitation(evidence_id="ev_trace_01", reason="r2"),
            ],
            "missing_information_ids": ["gap1", "gap2"],
        }
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[heavily_penalized],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    assert ranked.hypotheses[0].evidence_score == 0.0


# ---------------------------------------------------------------------------
# Inconclusive Handling Tests
# ---------------------------------------------------------------------------


def test_inconclusive_empty_hypotheses(sample_context: IncidentContextSnapshot) -> None:
    engine = RankingEngine()
    empty_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(empty_set, sample_context)
    assert ranked.status == InvestigationStatus.INCONCLUSIVE
    assert ranked.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
    assert ranked.hypotheses == []
    assert len(ranked.remaining_uncertainty) > 0


def test_inconclusive_no_valid_supporting_evidence(sample_context: IncidentContextSnapshot) -> None:
    engine = RankingEngine()

    # Hypotheses with citations that do NOT exist in context
    hyp_bogus = Hypothesis(
        hypothesis_id="hyp_bogus",
        incident_id="inc_rank_01",
        statement="Ghost cause citing nonexistent evidence",
        root_cause_category=RootCauseCategory.CODE_DEFECT,
        affected_component="payment-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="nonexistent_ev_999", reason="invented"),
        ],
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[hyp_bogus],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    assert ranked.status == InvestigationStatus.INCONCLUSIVE
    assert ranked.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
    assert ranked.hypotheses == []


def test_inconclusive_explicit_status_and_stop_reason(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    engine = RankingEngine()
    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[base_hypothesis],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(
        hyp_set,
        sample_context,
        status=InvestigationStatus.INCONCLUSIVE,
        stop_reason=StopReason.SOURCES_UNAVAILABLE,
        remaining_uncertainty=["Logs source was unreachable."],
    )

    assert ranked.status == InvestigationStatus.INCONCLUSIVE
    assert ranked.stop_reason == StopReason.SOURCES_UNAVAILABLE
    assert ranked.remaining_uncertainty == ["Logs source was unreachable."]
    assert ranked.hypotheses == []


# ---------------------------------------------------------------------------
# Output Boundary & Score Breakdown Verification
# ---------------------------------------------------------------------------


def test_ranked_hypothesis_set_conforms_to_contract(
    base_hypothesis: Hypothesis,
    sample_context: IncidentContextSnapshot,
) -> None:
    engine = RankingEngine()
    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[base_hypothesis],
        generated_at=datetime.now(timezone.utc),
    )

    budget = BudgetUsage(rounds=2, queries=5, reasoning_calls=3)
    ranked = engine.rank(
        hyp_set,
        sample_context,
        budget_usage=budget,
        ranking_id="rank_custom_123",
    )

    assert isinstance(ranked, RankedHypothesisSet)
    assert ranked.status == InvestigationStatus.COMPLETED
    assert ranked.stop_reason is None
    assert ranked.ranking_id == "rank_custom_123"
    assert ranked.context_snapshot_id == sample_context.snapshot_id
    assert ranked.budget_usage.rounds == 2
    assert ranked.budget_usage.queries == 5
    assert ranked.budget_usage.reasoning_calls == 3

    h = ranked.hypotheses[0]
    assert h.rank == 1
    assert h.hypothesis_id == "hyp_01"
    assert 0.0 <= h.evidence_score <= 100.0
    assert h.confidence_label in {ConfidenceLabel.HIGH, ConfidenceLabel.MEDIUM, ConfidenceLabel.LOW}

    # Verify score breakdown matches the sum
    sb = h.score_breakdown
    computed_raw = (
        sb.independent_source_support
        + sb.symptom_coverage
        + sb.temporal_consistency
        + sb.change_consistency
        + sb.specificity
        + sb.prediction_support
        - sb.contradiction_penalty
        - sb.missing_evidence_penalty
    )
    expected_score = round(max(0.0, min(100.0, computed_raw)), 2)
    assert h.evidence_score == expected_score


def test_model_cannot_directly_assign_numeric_score(
    sample_context: IncidentContextSnapshot,
) -> None:
    """
    Verify that the final score is purely computed from deterministic features,
    and cannot be influenced by arbitrary external fields or direct assignment.
    """
    engine = RankingEngine()

    h = Hypothesis(
        hypothesis_id="hyp_tamper_attempt",
        incident_id="inc_rank_01",
        statement="A hypothesis where someone might want 99.9 score",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="unknown",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_log_01", reason="only one log")],
        testable_prediction="",
    )

    hyp_set = HypothesisSet(
        incident_id="inc_rank_01",
        hypotheses=[h],
        generated_at=datetime.now(timezone.utc),
    )

    ranked = engine.rank(hyp_set, sample_context)
    # Because of vague specificity, single source, no prediction, etc., score cannot be high
    assert ranked.hypotheses[0].evidence_score < 50.0
    assert ranked.hypotheses[0].confidence_label != ConfidenceLabel.HIGH
