"""
Unit tests for Phase 7: Make Confidence Explainable.

Validates:
1. Mathematical reproducibility: Every score equals sum(positives) - sum(penalties).
2. Missing causal evidence visibly reduces change_consistency and causal_score.
3. HIGH confidence is blocked and capped at MEDIUM for change regressions without causal support, setting capped_reason.
4. Contradiction and missing evidence penalties are explicitly deducted and visible.
5. Symptom and causal sub-scores are computed within [0.0, 100.0].
6. ScoreBreakdown.breakdown_rows() provides full transparent items for display.
7. Markdown report and CLI presentation render breakdown tables and sub-scores.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.common import (
    ConfidenceLabel,
    EvidenceRole,
    EvidenceType,
    HypothesisStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceType,
)
from contracts.evidence.schemas import (
    EvidenceProvenance,
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
    ScoreBreakdown,
)
from reasoning.ranking.ranking_engine import RankingEngine, RankingWeights
from recoverit.runner import InvestigationResult


@pytest.fixture
def base_context() -> IncidentContextSnapshot:
    """Fixture providing a context with both causal and symptom evidence."""
    now = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)
    earlier = datetime(2026, 9, 12, 10, 25, 0, tzinfo=timezone.utc)
    prov = EvidenceProvenance(
        batch_id="batch_01",
        query_id="query_01",
        source_record_id="rec_01",
        source_adapter="test_adapter",
        raw_payload_hash="hash_01",
    )
    qual = EvidenceQuality(
        reliability=Reliability.HIGH,
    )

    return IncidentContextSnapshot(
        snapshot_id="ctx_test_p7",
        incident_id="inc_test_p7",
        revision=1,
        created_at=now,
        incident=IncidentSummary(
            service="payment-api",
            environment="production",
            severity=Severity.CRITICAL,
            detected_at=now,
            summary="HTTP 500 spike after deployment",
        ),
        evidence=[
            EvidenceSummaryProjection(
                evidence_id="ev_deploy_01",
                source_type=SourceType.DEPLOYMENTS,
                evidence_type=EvidenceType.DEPLOYMENT_EVENT,
                event_time=earlier,
                summary="Deployment dep_v2.4.1 succeeded",
                quality=qual,
            ),
            EvidenceSummaryProjection(
                evidence_id="ev_log_err_01",
                source_type=SourceType.LOGS,
                evidence_type=EvidenceType.ERROR_EVENT,
                event_time=now,
                summary="Database connection pool timeout",
                quality=qual,
            ),
            EvidenceSummaryProjection(
                evidence_id="ev_metric_500",
                source_type=SourceType.METRICS,
                evidence_type=EvidenceType.METRIC_ANOMALY,
                event_time=now,
                summary="HTTP 500 error rate spiked to 35%",
                quality=qual,
            ),
        ],
        timeline=[],
        relationships=[],
        source_coverage={},
        warnings=[],
    )


def test_score_is_strictly_reproducible_from_breakdown(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 1: Every score can be reproduced from the displayed breakdown.
    score == clamp(sum(positive) - sum(penalties), 0, 100).
    """
    engine = RankingEngine()
    hyp = Hypothesis(
        hypothesis_id="h_rep",
        incident_id="inc_test_p7",
        statement="Recent deployment introduced database configuration error.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-api",
        testable_prediction="Database connection timeout logs appear after deployment.",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_deploy_01",
                role=EvidenceRole.CAUSE,
                reason="Deployment preceded error spike.",
            ),
            EvidenceCitation(
                evidence_id="ev_log_err_01",
                role=EvidenceRole.EFFECT,
                reason="Observed error logs.",
            ),
        ],
        contradicting_evidence=[],
        missing_information_ids=[],
    )

    breakdown = engine.calculate_breakdown(hyp, base_context)
    computed_score = engine.calculate_evidence_score(breakdown)

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
    expected_score = round(max(0.0, min(100.0, positive_sum - penalties)), 2)

    assert computed_score == expected_score
    # Verify breakdown_rows contains all 8 items and reproduces the arithmetic
    rows = breakdown.breakdown_rows()
    assert len(rows) == 8
    row_sum = 0.0
    for name, sign, val in rows:
        if sign == "+":
            row_sum += val
        elif sign == "-":
            row_sum -= val
    assert round(max(0.0, min(100.0, row_sum)), 2) == computed_score


def test_missing_causal_evidence_visibly_reduces_confidence_and_causal_score(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 2: Missing causal evidence visibly reduces confidence and causal_score.
    """
    engine = RankingEngine()

    # Hypothesis WITH direct causal evidence
    hyp_with_cause = Hypothesis(
        hypothesis_id="h_causal",
        incident_id="inc_test_p7",
        statement="Recent deployment updated configuration with invalid pool parameters.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_deploy_01",
                role=EvidenceRole.CAUSE,
                reason="Deployment change introduced issue.",
            ),
            EvidenceCitation(
                evidence_id="ev_log_err_01",
                role=EvidenceRole.EFFECT,
                reason="Runtime error symptom.",
            ),
        ],
    )

    # Hypothesis WITHOUT direct causal evidence (only symptom logs cited)
    hyp_without_cause = Hypothesis(
        hypothesis_id="h_symptom_only",
        incident_id="inc_test_p7",
        statement="Recent deployment updated configuration with invalid pool parameters.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_log_err_01",
                role=EvidenceRole.EFFECT,
                reason="Only symptom cited.",
            ),
        ],
        missing_information_ids=["need_deploy_record"],
    )

    bd_with = engine.calculate_breakdown(hyp_with_cause, base_context)
    bd_without = engine.calculate_breakdown(hyp_without_cause, base_context)

    score_with = engine.calculate_evidence_score(bd_with)
    score_without = engine.calculate_evidence_score(bd_without)

    # Change consistency is visibly depressed when no change evidence is cited
    assert bd_without.change_consistency < bd_with.change_consistency
    # Causal score is visibly depressed
    assert bd_without.causal_score < bd_with.causal_score
    # Missing evidence penalty is applied
    assert bd_without.missing_evidence_penalty > 0.0
    # Overall score is strictly lower
    assert score_without < score_with


def test_high_confidence_cannot_be_assigned_without_direct_causal_support(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 3: HIGH confidence cannot be assigned without direct causal support.
    When a change hypothesis achieves a score >= 70 but lacks causal evidence,
    its confidence is capped at MEDIUM and capped_reason is populated.
    """
    # Create an engine with artificially high weights so score >= 70 even without causal cite
    high_weights = RankingWeights(
        independent_source_support=35.0,
        symptom_coverage=35.0,
        temporal_consistency=10.0,
        change_consistency=10.0,
        specificity=10.0,
        prediction_support=10.0,
        contradiction_penalty=0.0,
        missing_evidence_penalty=0.0,
    )
    engine = RankingEngine(weights=high_weights)

    hyp_symptom_only = Hypothesis(
        hypothesis_id="h_high_no_cause",
        incident_id="inc_test_p7",
        statement="Configuration regression in payment service causing failures.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-api",
        testable_prediction="Database connection timeout logs appear after deployment.",
        supporting_evidence=[
            # Multiple sources, but only symptoms (logs + metrics), no deployment or change
            EvidenceCitation(
                evidence_id="ev_log_err_01",
                role=EvidenceRole.EFFECT,
                reason="Symptom logs.",
            ),
            EvidenceCitation(
                evidence_id="ev_metric_500",
                role=EvidenceRole.EFFECT,
                reason="Symptom metrics.",
            ),
        ],
    )

    breakdown = engine.calculate_breakdown(hyp_symptom_only, base_context)
    score = engine.calculate_evidence_score(breakdown)

    # Raw score exceeds high threshold (>= 70.0)
    assert score >= engine.thresholds.high
    # But label MUST be capped at MEDIUM
    label = engine.derive_confidence_label(score, hypothesis=hyp_symptom_only, context=base_context)
    assert label == ConfidenceLabel.MEDIUM
    # And capped_reason is populated with clear explanation
    assert breakdown.capped_reason is not None
    assert "capped at MEDIUM" in breakdown.capped_reason


def test_penalties_and_contradictions_are_explicitly_displayed_not_hidden(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 4: Penalties and contradictions are displayed, not hidden.
    """
    engine = RankingEngine()
    hyp_with_penalties = Hypothesis(
        hypothesis_id="h_penalties",
        incident_id="inc_test_p7",
        statement="Hardware failure on node caused outage.",
        root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
        affected_component="worker-node-1",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_log_err_01",
                role=EvidenceRole.EFFECT,
                reason="Error observed.",
            ),
        ],
        contradicting_evidence=[
            EvidenceCitation(
                evidence_id="ev_deploy_01",
                role=EvidenceRole.CONTRADICTION,
                reason="Deployment occurred simultaneously, contradicting pure hardware failure.",
            ),
        ],
        missing_information_ids=["need_node_health", "need_kernel_dmesg"],
    )

    breakdown = engine.calculate_breakdown(hyp_with_penalties, base_context)

    # Both penalties must be > 0.0
    assert breakdown.contradiction_penalty > 0.0
    assert breakdown.missing_evidence_penalty > 0.0

    # In breakdown_rows, both must appear with '-' sign
    rows = dict([(r[0], (r[1], r[2])) for r in breakdown.breakdown_rows()])
    assert rows["Contradictions"][0] == "-"
    assert rows["Contradictions"][1] == breakdown.contradiction_penalty
    assert rows["Missing causal evidence"][0] == "-"
    assert rows["Missing causal evidence"][1] == breakdown.missing_evidence_penalty


def test_symptom_and_causal_sub_scores_bounds_and_validity(
    base_context: IncidentContextSnapshot,
) -> None:
    """
    Criterion 5: Symptom and causal confidence sub-scores are within [0.0, 100.0].
    """
    engine = RankingEngine()
    hyp = Hypothesis(
        hypothesis_id="h_subs",
        incident_id="inc_test_p7",
        statement="Configuration regression on payment-api.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-api",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_deploy_01", role=EvidenceRole.CAUSE, reason="Causal deployment."),
            EvidenceCitation(evidence_id="ev_log_err_01", role=EvidenceRole.EFFECT, reason="Symptom log."),
        ],
    )

    breakdown = engine.calculate_breakdown(hyp, base_context)
    assert 0.0 <= breakdown.symptom_score <= 100.0
    assert 0.0 <= breakdown.causal_score <= 100.0
    assert breakdown.symptom_score > 0.0
    assert breakdown.causal_score > 0.0


def test_markdown_report_renders_confidence_breakdown_and_sub_scores() -> None:
    """
    Criterion 6: InvestigationResult.to_markdown_report renders sub-scores and breakdown table.
    """
    breakdown = ScoreBreakdown(
        independent_source_support=21.25,
        symptom_coverage=15.00,
        temporal_consistency=15.00,
        change_consistency=15.00,
        specificity=8.00,
        prediction_support=15.00,
        contradiction_penalty=0.00,
        missing_evidence_penalty=0.00,
        symptom_score=75.0,
        causal_score=85.0,
        capped_reason=None,
    )

    res = InvestigationResult(
        incident_id="inc-test_01",
        service="payment-api",
        summary="Test incident",
        status="completed",
        stop_reason=None,
        execution_time_seconds=0.05,
        ranked_hypotheses=[
            {
                "rank": 1,
                "hypothesis_id": "h1",
                "statement": "Database misconfiguration",
                "root_cause_category": "configuration_regression",
                "affected_component": "payment-api",
                "evidence_score": 89.25,
                "confidence_label": "high",
                "supporting_evidence": [{"evidence_id": "ev_01", "reason": "cause"}],
                "contradicting_evidence": [],
                "unresolved_questions": [],
                "score_breakdown": breakdown.model_dump(),
            }
        ],
        timeline_events=[],
        evidence_items=[],
        diff_excerpts={},
        log_excerpts=[],
        budget_usage={},
        provider_used="deterministic-preset",
        completion_criteria={"independent_sources": True},
        unresolved_criteria=[],
    )

    md = res.to_markdown_report()
    assert "Symptom Confidence:" in md
    assert "75.0%" in md
    assert "Causal Confidence:" in md
    assert "85.0%" in md
    assert "Confidence Breakdown:" in md
    assert "Independent sources" in md
    assert "+21.25" in md
    assert "Direct change evidence" in md
    assert "+15.00" in md
    assert "Final Evidence Score" in md
    assert "89.25 / 100" in md
