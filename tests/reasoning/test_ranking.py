"""Tests for deterministic ranking engine and feature calculators."""

import pytest
from datetime import datetime, timezone

from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceType,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import Hypothesis, HypothesisSet, EvidenceCitation
from reasoning.ranking.feature_calculators import (
    calculate_independent_source_support,
    calculate_symptom_coverage,
    calculate_contradiction_penalty,
    calculate_missing_evidence_penalty,
)
from reasoning.ranking.ranking_engine import (
    RankingEngine,
    RankingWeights,
)


def _make_snapshot(records=None, timeline=None, symptoms=None):
    if records is None:
        records = []
    if timeline is None:
        timeline = []
    if symptoms is None:
        symptoms = ["500 internal server error"]
    rec_dict = {r.evidence_id: r for r in records}
    
    summary = IncidentSummary(
        incident_id="inc-100",
        service="web-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime.now(timezone.utc),
        summary="HTTP 500 error spike",
    )

    return IncidentContextSnapshot(
        snapshot_id="snap-100",
        incident_id="inc-100",
        revision=1,
        created_at=datetime.now(timezone.utc),
        incident=summary,
        symptoms=symptoms,
        evidence_records=rec_dict,
        timeline=timeline,
    )


def test_calculate_independent_source_support():
    h = Hypothesis(
        hypothesis_id="hyp-1",
        incident_id="inc-100",
        statement="Test Hyp",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="auth-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev-1", reason="log error"),
            EvidenceCitation(evidence_id="ev-2", reason="k8s event"),
            EvidenceCitation(evidence_id="ev-3", reason="metric spike"),
        ],
    )
    rec1 = EvidenceSummaryProjection(evidence_id="ev-1", source_type=SourceType.LOGS, evidence_type=EvidenceType.INFO_EVENT, summary="", quality=EvidenceQuality(reliability=Reliability.HIGH))
    rec2 = EvidenceSummaryProjection(evidence_id="ev-2", source_type=SourceType.DEPLOYMENTS, evidence_type=EvidenceType.DEPLOYMENT_EVENT, summary="", quality=EvidenceQuality(reliability=Reliability.HIGH))
    rec3 = EvidenceSummaryProjection(evidence_id="ev-3", source_type=SourceType.METRICS, evidence_type=EvidenceType.METRIC_ANOMALY, summary="", quality=EvidenceQuality(reliability=Reliability.HIGH))
    snapshot = _make_snapshot(records=[rec1, rec2, rec3])

    # 3 distinct SourceTypes: LOGS, DEPLOYMENTS, METRICS -> 3/3 = 1.0
    assert calculate_independent_source_support(h, snapshot) == 1.0


def test_calculate_symptom_coverage():
    h = Hypothesis(
        hypothesis_id="hyp-1",
        incident_id="inc-100",
        statement="Test Hyp",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="auth-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev-1", reason="log error"),
        ],
    )
    rec1 = EvidenceSummaryProjection(evidence_id="ev-1", source_type=SourceType.LOGS, evidence_type=EvidenceType.ERROR_EVENT, summary="Found 500 internal server error", quality=EvidenceQuality(reliability=Reliability.HIGH))
    snapshot = _make_snapshot(records=[rec1], symptoms=["500 internal server error"])

    score = calculate_symptom_coverage(h, snapshot)
    assert score == 1.0


def test_calculate_contradiction_penalty():
    h = Hypothesis(
        hypothesis_id="hyp-1",
        incident_id="inc-100",
        statement="Test Hyp",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="auth-service",
        contradicting_evidence=[
            EvidenceCitation(evidence_id="ev-c1", reason="contradicts"),
            EvidenceCitation(evidence_id="ev-c2", reason="contradicts"),
        ],
    )
    rec_c1 = EvidenceSummaryProjection(evidence_id="ev-c1", source_type=SourceType.METRICS, evidence_type=EvidenceType.METRIC_ANOMALY, summary="", quality=EvidenceQuality(reliability=Reliability.HIGH))
    rec_c2 = EvidenceSummaryProjection(evidence_id="ev-c2", source_type=SourceType.LOGS, evidence_type=EvidenceType.ERROR_EVENT, summary="", quality=EvidenceQuality(reliability=Reliability.LOW))
    snapshot = _make_snapshot(records=[rec_c1, rec_c2])

    penalty = calculate_contradiction_penalty(h, snapshot)
    assert pytest.approx(penalty, 0.01) == 1.0  # 2 contradicting evidence -> min(1.0, 2/2) = 1.0


def test_calculate_missing_evidence_penalty():
    h = Hypothesis(
        hypothesis_id="hyp-1",
        incident_id="inc-100",
        statement="Test Hyp",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="auth-service",
        missing_information_ids=["gap-1", "gap-2"],
    )
    snapshot = _make_snapshot()
    # 2 missing gaps -> min(1.0, 2 / 3.0) = 0.666
    assert pytest.approx(calculate_missing_evidence_penalty(h, snapshot), 0.01) == 0.666


def test_ranking_engine_deterministic_ranking():
    engine = RankingEngine()

    h1 = Hypothesis(
        hypothesis_id="hyp-1",
        incident_id="inc-100",
        statement="Database Connection Pool Exhausted causing HTTP 500 errors.",
        root_cause_category=RootCauseCategory.RESOURCE_EXHAUSTION,
        affected_component="db-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev-1", reason="500 log"),
            EvidenceCitation(evidence_id="ev-2", reason="metric error"),
        ],
        testable_prediction="DB connection logs show connection refused",
    )
    h2 = Hypothesis(
        hypothesis_id="hyp-2",
        incident_id="inc-100",
        statement="High CPU Usage",
        root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
        affected_component="web-service",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev-3", reason="cpu metric"),
        ],
    )

    rec1 = EvidenceSummaryProjection(evidence_id="ev-1", source_type=SourceType.LOGS, evidence_type=EvidenceType.ERROR_EVENT, summary="500 internal server error connection refused", quality=EvidenceQuality(reliability=Reliability.HIGH))
    rec2 = EvidenceSummaryProjection(evidence_id="ev-2", source_type=SourceType.METRICS, evidence_type=EvidenceType.METRIC_ANOMALY, summary="500 internal server error count spike", quality=EvidenceQuality(reliability=Reliability.HIGH))
    rec3 = EvidenceSummaryProjection(evidence_id="ev-3", source_type=SourceType.METRICS, evidence_type=EvidenceType.METRIC_ANOMALY, summary="CPU high", quality=EvidenceQuality(reliability=Reliability.HIGH))

    snapshot = _make_snapshot(records=[rec1, rec2, rec3], symptoms=["500 internal server error"])
    hyp_set = HypothesisSet(incident_id="inc-100", generated_at=datetime.now(timezone.utc), hypotheses=[h1, h2])

    result = engine.rank(hyp_set, snapshot)

    assert len(result.hypotheses) == 2
    assert result.hypotheses[0].hypothesis_id == "hyp-1"
    assert result.status == InvestigationStatus.COMPLETED
    assert result.stop_reason is None


def test_ranking_engine_stable_tie_breaking():
    engine = RankingEngine()

    # Create two identical hypotheses in terms of evidence score
    h_b = Hypothesis(
        hypothesis_id="hyp-b",
        incident_id="inc-100",
        statement="Hypothesis B",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="service-b",
        supporting_evidence=[EvidenceCitation(evidence_id="ev-1", reason="same")],
    )
    h_a = Hypothesis(
        hypothesis_id="hyp-a",
        incident_id="inc-100",
        statement="Hypothesis A",
        root_cause_category=RootCauseCategory.UNKNOWN,
        affected_component="service-a",
        supporting_evidence=[EvidenceCitation(evidence_id="ev-1", reason="same")],
    )

    rec1 = EvidenceSummaryProjection(evidence_id="ev-1", source_type=SourceType.METRICS, evidence_type=EvidenceType.METRIC_ANOMALY, summary="", quality=EvidenceQuality(reliability=Reliability.HIGH))
    snapshot = _make_snapshot(records=[rec1], symptoms=[])
    hyp_set = HypothesisSet(incident_id="inc-100", generated_at=datetime.now(timezone.utc), hypotheses=[h_b, h_a])

    result = engine.rank(hyp_set, snapshot)

    # Tie-breaking by hypothesis_id ascending
    assert result.hypotheses[0].hypothesis_id == "hyp-a"
    assert result.hypotheses[1].hypothesis_id == "hyp-b"


def test_ranking_engine_inconclusive_handling():
    engine = RankingEngine()

    # No supporting evidence for any hypothesis
    h1 = Hypothesis(hypothesis_id="hyp-1", incident_id="inc-100", statement="Hyp 1", root_cause_category=RootCauseCategory.UNKNOWN, affected_component="svc")
    h2 = Hypothesis(hypothesis_id="hyp-2", incident_id="inc-100", statement="Hyp 2", root_cause_category=RootCauseCategory.UNKNOWN, affected_component="svc")

    snapshot = _make_snapshot(records=[], symptoms=["500 internal server error"])
    hyp_set = HypothesisSet(incident_id="inc-100", generated_at=datetime.now(timezone.utc), hypotheses=[h1, h2])

    result = engine.rank(hyp_set, snapshot)

    assert result.status == InvestigationStatus.INCONCLUSIVE
    assert result.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
    assert len(result.hypotheses) == 0 or all(rh.evidence_score == 0.0 for rh in result.hypotheses)


def test_ranking_engine_configurable_weights():
    custom_weights = RankingWeights(independent_source_support=1.0, symptom_coverage=0.0)
    engine = RankingEngine(weights=custom_weights)

    assert engine.weights.independent_source_support == 1.0
    assert engine.weights.symptom_coverage == 0.0
