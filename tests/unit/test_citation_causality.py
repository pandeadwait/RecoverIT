"""
Unit tests for Phase 4: Causal Reasoning Over Correlation & Strengthened Citation Validation.

Verifies:
- EvidenceRole enum taxonomy.
- EvidenceCitation default role and serialization.
- CitationValidator warnings on improper causal citations (logs/metrics as 'cause').
- CitationValidator warnings on missing direct change evidence for change hypotheses.
- RankingEngine confidence capping: logs and metrics alone cannot produce HIGH confidence for configuration regression.
- RankingEngine confidence achievement: citing relevant change achieves HIGH confidence when score warrants it.
- FakeReasoningProvider presets populate evidence roles.
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
from contracts.errors.schemas import CITATION_INVALID
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    EvidenceCitation,
    Hypothesis,
    HypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from reasoning.hypotheses.citation_validator import CitationValidator
from reasoning.provider.fake_provider import FakeReasoningProvider
from reasoning.ranking.ranking_engine import RankingEngine


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_causal_01",
        external_alert_id="alt_ext_01",
        service="checkout-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="High error rate on /checkout",
        labels={"tier": "1"},
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    t0 = datetime(2026, 9, 12, 9, 50, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)

    e_deploy = EvidenceSummaryProjection(
        evidence_id="ev_deploy_01",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=t0,
        summary="Deployment v2.4.1 completed with updated database pool configs.",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e_config = EvidenceSummaryProjection(
        evidence_id="ev_config_01",
        source_type=SourceType.CONFIGURATION,
        evidence_type=EvidenceType.CONFIGURATION_CHANGE,
        event_time=t0,
        summary="Config key db.max_connections reduced from 100 to 5.",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e_log = EvidenceSummaryProjection(
        evidence_id="ev_log_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=t1,
        summary="HTTP 500 connection pool exhausted on checkout-api.",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    e_metric = EvidenceSummaryProjection(
        evidence_id="ev_metric_01",
        source_type=SourceType.METRICS,
        evidence_type=EvidenceType.METRIC_ANOMALY,
        event_time=t1,
        summary="Metric http_500_rate surged to 35% on checkout-api.",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )

    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_causal_01",
        revision=1,
        created_at=t1,
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            summary=sample_incident.summary,
            detected_at=sample_incident.detected_at,
        ),
        evidence=[e_deploy, e_config, e_log, e_metric],
    )


def test_evidence_role_enum():
    """Verify all 5 evidence roles are supported."""
    assert EvidenceRole.CAUSE == "cause"
    assert EvidenceRole.EFFECT == "effect"
    assert EvidenceRole.CORRELATION == "correlation"
    assert EvidenceRole.CONTRADICTION == "contradiction"
    assert EvidenceRole.CONTEXT == "context"


def test_evidence_citation_role_default_and_explicit():
    """Verify EvidenceCitation defaults role to CORRELATION and accepts explicit role."""
    cit_default = EvidenceCitation(evidence_id="ev_log_01", reason="Observed errors")
    assert cit_default.role == EvidenceRole.CORRELATION

    cit_explicit = EvidenceCitation(
        evidence_id="ev_config_01",
        reason="Direct pool limit change",
        role=EvidenceRole.CAUSE,
    )
    assert cit_explicit.role == EvidenceRole.CAUSE

    # JSON roundtrip
    dumped = cit_explicit.model_dump_json()
    loaded = EvidenceCitation.model_validate_json(dumped)
    assert loaded.role == EvidenceRole.CAUSE


def test_validator_warns_on_symptom_cited_as_cause_for_change_hypothesis(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
):
    """Logs/metrics cited as direct 'cause' for a change regression produce a visible validation warning."""
    validator = CitationValidator()

    hyp = Hypothesis(
        hypothesis_id="hyp_flawed_causality",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Recent deployment broke checkout because 500 error log occurred.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_log_01",
                reason="500 log is the cause",
                role=EvidenceRole.CAUSE,  # INVALID: log cannot be direct cause of config regression
            )
        ],
        testable_prediction="Check logs.",
    )

    report = validator.validate_hypothesis(hyp, sample_context)
    # The citation is valid (evidence exists), but emits a visible warning for improper causal role
    assert report.is_valid
    assert len(report.warnings) >= 1
    causal_warnings = [
        w for w in report.warnings
        if "cited as direct 'cause'" in w.message or "logs/metrics can only demonstrate impact" in w.message
    ]
    assert len(causal_warnings) == 1
    assert causal_warnings[0].code == CITATION_INVALID


def test_validator_warns_when_configuration_regression_lacks_change_evidence(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
):
    """A configuration_regression hypothesis citing only logs/metrics generates a warning."""
    validator = CitationValidator()

    hyp = Hypothesis(
        hypothesis_id="hyp_no_config_cite",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Configuration regression in connection limits.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_log_01",
                reason="Error logs observed",
                role=EvidenceRole.EFFECT,
            ),
            EvidenceCitation(
                evidence_id="ev_metric_01",
                reason="Error rate metric elevated",
                role=EvidenceRole.CORRELATION,
            ),
        ],
        testable_prediction="Check config.",
    )

    report = validator.validate_hypothesis(hyp, sample_context)
    assert report.is_valid
    assert any("does not cite configuration or code-change evidence" in w.message for w in report.warnings)
    assert any("lacks direct configuration/change evidence" in a for a in report.uncited_assumptions)


def test_ranking_confidence_capped_without_change_evidence(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
):
    """
    Acceptance Criterion:
    Logs and metrics alone cannot produce HIGH confidence for a configuration regression.
    """
    engine = RankingEngine()

    # Hypothesis only citing logs and metrics
    hyp_symptoms_only = Hypothesis(
        hypothesis_id="hyp_symptoms_only",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Database connection configuration regression caused checkout failure.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_log_01", reason="500 errors logged", role=EvidenceRole.EFFECT),
            EvidenceCitation(evidence_id="ev_metric_01", reason="Metrics spiked", role=EvidenceRole.CORRELATION),
        ],
        testable_prediction="Database pool size is too small for incoming traffic.",
        status=HypothesisStatus.ACTIVE,
    )

    hyp_set = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=[hyp_symptoms_only],
        generated_at=datetime.now(timezone.utc),
    )

    ranked_set = engine.rank(hyp_set, sample_context)
    ranked = ranked_set.hypotheses[0]

    # Even if score might be above 70.0 from symptom coverage + specificity + prediction,
    # confidence MUST NOT be HIGH without direct change evidence.
    assert ranked.confidence_label in {ConfidenceLabel.MEDIUM, ConfidenceLabel.LOW}
    assert ranked.confidence_label != ConfidenceLabel.HIGH


def test_ranking_achieves_high_confidence_when_change_is_cited(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
):
    """
    Acceptance Criterion:
    A configuration-regression hypothesis becomes strongly supported (HIGH confidence)
    after citing the relevant change.
    """
    engine = RankingEngine()

    # Hypothesis citing both direct configuration change AND symptom evidence
    hyp_with_change = Hypothesis(
        hypothesis_id="hyp_with_change",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Database connection pool configuration regression introduced by recent deployment.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_config_01", reason="Config reduced connection limit to 5", role=EvidenceRole.CAUSE),
            EvidenceCitation(evidence_id="ev_deploy_01", reason="Deployment deployed bad configuration", role=EvidenceRole.CAUSE),
            EvidenceCitation(evidence_id="ev_log_01", reason="Connection pool timeout errors logged", role=EvidenceRole.EFFECT),
            EvidenceCitation(evidence_id="ev_metric_01", reason="500 rate surged following config change", role=EvidenceRole.EFFECT),
        ],
        testable_prediction="Database max connections config in checkout-api equals 5.",
        status=HypothesisStatus.ACTIVE,
    )

    hyp_set = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=[hyp_with_change],
        generated_at=datetime.now(timezone.utc),
    )

    ranked_set = engine.rank(hyp_set, sample_context)
    ranked = ranked_set.hypotheses[0]

    assert ranked.evidence_score >= 70.0
    assert ranked.confidence_label == ConfidenceLabel.HIGH


@pytest.mark.asyncio
async def test_fake_provider_hypotheses_populate_roles(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
):
    """Verify FakeReasoningProvider populates roles across presets."""
    for p in ["database-outage", "resource-exhaustion", "dependency-incompatibility", "coincidental-deployment", "deployment-regression"]:
        provider = FakeReasoningProvider(preset=p)
        res = await provider.generate_hypotheses(
            incident=sample_incident,
            context=sample_context,
            limits=InvestigationBudget(minimum_hypotheses=2, maximum_hypotheses=4),
        )
        assert len(res.hypotheses) >= 2
        for h in res.hypotheses:
            for cit in h.supporting_evidence:
                assert cit.role in {r.value for r in EvidenceRole}
