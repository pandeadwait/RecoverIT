"""
Unit tests for HypothesisGenerator, HypothesisReviser, and CitationValidator.

Verifies:
- Multiple hypotheses are generated for a scenario.
- Bias mitigation ensures a non-change-related alternative is included.
- Supporting and contradicting evidence remain separate per hypothesis.
- Invalid evidence IDs are rejected/filtered.
- Cross-incident evidence IDs are rejected.
- Revision preserves rejected hypotheses (never deleted).
- Revision monotonically increments the revision number.
- Citation validator catches all error types: missing evidence, cross-incident,
  dual support/contradiction conflict, missing reasons, and tracks low-reliability/truncation.

See WORK_DIVISION.md §8.5, §8.10 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.common import (
    EvidenceType,
    HypothesisStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceType,
)
from contracts.errors.schemas import (
    CITATION_INVALID,
    CROSS_INCIDENT_REFERENCE,
)
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
from reasoning.hypotheses.generator import (
    CHANGE_RELATED_CATEGORIES,
    HypothesisGenerator,
)
from reasoning.hypotheses.reviser import HypothesisReviser
from reasoning.provider.fake_provider import FakeReasoningProvider


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_hyp_01",
        external_alert_id="alt_ext_01",
        service="checkout-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="High error rate on /pay",
        labels={"tier": "1"},
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    e1 = EvidenceSummaryProjection(
        evidence_id="ev_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        summary="HTTP 500 connection timeout to database",
        quality=EvidenceQuality(reliability=Reliability.HIGH, truncated_source=False),
    )
    e2 = EvidenceSummaryProjection(
        evidence_id="ev_02",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=datetime(2026, 9, 12, 9, 55, 0, tzinfo=timezone.utc),
        summary="Deployment v2.4.0 completed",
        quality=EvidenceQuality(reliability=Reliability.MEDIUM, truncated_source=True),
    )
    e3 = EvidenceSummaryProjection(
        evidence_id="ev_low_rel",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 2, 0, tzinfo=timezone.utc),
        summary="Unconfirmed third-party error report",
        quality=EvidenceQuality(reliability=Reliability.LOW, truncated_source=False),
    )

    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_hyp_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 3, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        timeline=[],
        evidence=[e1, e2, e3],
        source_coverage={SourceType.LOGS: SourceCoverageStatus.AVAILABLE},
    )


@pytest.fixture
def sample_limits() -> InvestigationBudget:
    return InvestigationBudget(
        max_rounds=3,
        max_queries=6,
        minimum_hypotheses=2,
        maximum_hypotheses=4,
    )


# ---------------------------------------------------------------------------
# CitationValidator Tests
# ---------------------------------------------------------------------------


def test_validator_detects_nonexistent_evidence_id(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    bad_hyp = Hypothesis(
        hypothesis_id="hyp_bad_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Database connection failed.",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_nonexistent_99",
                reason="This evidence does not exist.",
            )
        ],
        testable_prediction="Check database logs.",
    )

    report = validator.validate_hypothesis(bad_hyp, sample_context)
    assert not report.is_valid
    assert any(err.code == CITATION_INVALID for err in report.errors)
    assert "does not exist" in report.errors[0].message


def test_validator_rejects_cross_incident_evidence(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    # Evidence ID prefixed with another incident ID
    cross_hyp = Hypothesis(
        hypothesis_id="hyp_cross_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Payment gateway failure.",
        root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="inc_other_ev_101",
                reason="From another incident investigation.",
            )
        ],
        testable_prediction="Check gateway health.",
    )

    report = validator.validate_hypothesis(cross_hyp, sample_context)
    assert not report.is_valid
    assert any(err.code == CROSS_INCIDENT_REFERENCE for err in report.errors)


def test_validator_detects_dual_support_and_contradiction_without_explanation(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    # ev_01 is cited as both support and contradiction with identical/unexplained reason
    dual_hyp = Hypothesis(
        hypothesis_id="hyp_dual_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Configuration error.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_01", reason="Database timeout was logged.")
        ],
        contradicting_evidence=[
            EvidenceCitation(evidence_id="ev_01", reason="Database timeout was logged.")
        ],
        testable_prediction="Check config diff.",
    )

    report = validator.validate_hypothesis(dual_hyp, sample_context)
    assert not report.is_valid
    assert any(err.code == CITATION_INVALID for err in report.errors)
    assert "without an explicit differentiating explanation" in report.errors[0].message


def test_validator_accepts_dual_support_and_contradiction_with_explanation(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    # ev_01 has distinct differentiating reasons
    dual_hyp = Hypothesis(
        hypothesis_id="hyp_dual_02",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Configuration error.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_01",
                reason="Error counts surged after deployment, supporting causal link.",
            )
        ],
        contradicting_evidence=[
            EvidenceCitation(
                evidence_id="ev_01",
                reason="Preceded deployment timing partially, in contrast with pure regression.",
            )
        ],
        testable_prediction="Check config diff.",
    )

    report = validator.validate_hypothesis(dual_hyp, sample_context)
    assert report.is_valid


def test_validator_rejects_empty_reason(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    empty_reason_hyp = Hypothesis(
        hypothesis_id="hyp_empty_reason",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Database connection dropped.",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="checkout-api",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="   ")],
        testable_prediction="Inspect DB.",
    )

    report = validator.validate_hypothesis(empty_reason_hyp, sample_context)
    assert not report.is_valid
    assert any(err.code == CITATION_INVALID for err in report.errors)
    assert "lacks a reason" in report.errors[0].message


def test_validator_flags_low_reliability_and_truncated_evidence(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    hyp = Hypothesis(
        hypothesis_id="hyp_quality_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Transient network error.",
        root_cause_category=RootCauseCategory.INFRASTRUCTURE_FAILURE,
        affected_component="checkout-api",
        supporting_evidence=[
            EvidenceCitation(evidence_id="ev_02", reason="Deployment was truncated."),
            EvidenceCitation(evidence_id="ev_low_rel", reason="Low reliability report."),
        ],
        testable_prediction="Inspect network.",
    )

    report = validator.validate_hypothesis(hyp, sample_context)
    assert report.is_valid
    assert "ev_low_rel" in report.low_reliability_evidence_ids
    assert "ev_02" in report.truncated_evidence_ids


def test_validator_flags_uncited_assumptions(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    validator = CitationValidator()

    hyp = Hypothesis(
        hypothesis_id="hyp_uncited_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        statement="Speculative root cause with zero citations.",
        root_cause_category=RootCauseCategory.CODE_DEFECT,
        affected_component="checkout-api",
        supporting_evidence=[],
        testable_prediction="Speculative prediction.",
    )

    report = validator.validate_hypothesis(hyp, sample_context)
    assert len(report.uncited_assumptions) == 1
    assert "treated as assumption" in report.uncited_assumptions[0]


# ---------------------------------------------------------------------------
# HypothesisGenerator Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_hypotheses_generated_and_active(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
    sample_limits: InvestigationBudget,
) -> None:
    provider = FakeReasoningProvider(preset="deployment-regression")
    generator = HypothesisGenerator(provider=provider)

    hyp_set = await generator.generate(
        incident=sample_incident,
        context=sample_context,
        limits=sample_limits,
    )

    assert isinstance(hyp_set, HypothesisSet)
    assert len(hyp_set.hypotheses) >= sample_limits.minimum_hypotheses
    for h in hyp_set.hypotheses:
        assert h.status == HypothesisStatus.ACTIVE
        assert h.revision == 1
        assert h.statement
        assert h.affected_component
        assert h.testable_prediction

    # Verify JSON round-trip
    assert HypothesisSet.model_validate_json(hyp_set.model_dump_json()) == hyp_set


@pytest.mark.asyncio
async def test_bias_mitigation_includes_non_change_hypothesis(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
    sample_limits: InvestigationBudget,
) -> None:
    # All raw hypotheses are change-related (deployment / configuration)
    raw_hypotheses = [
        Hypothesis(
            hypothesis_id="hyp_01",
            incident_id=sample_incident.incident_id,
            revision=1,
            statement="Deployment broke DB config.",
            root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
            affected_component="checkout-api",
            supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="Errors after deploy")],
            testable_prediction="Check config.",
            status=HypothesisStatus.ACTIVE,
        ),
        Hypothesis(
            hypothesis_id="hyp_02",
            incident_id=sample_incident.incident_id,
            revision=1,
            statement="Code change introduced null pointer exception.",
            root_cause_category=RootCauseCategory.CODE_DEFECT,
            affected_component="checkout-api",
            supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="Errors logged")],
            testable_prediction="Check stack trace.",
            status=HypothesisStatus.ACTIVE,
        ),
    ]

    custom_set = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=raw_hypotheses,
        generated_at=datetime.now(timezone.utc),
    )

    provider = FakeReasoningProvider(custom_hypotheses=custom_set)
    generator = HypothesisGenerator(provider=provider)

    result_set = await generator.generate(
        incident=sample_incident,
        context=sample_context,
        limits=sample_limits,
    )

    # Verify that at least one hypothesis is NOT in CHANGE_RELATED_CATEGORIES
    non_change = [
        h for h in result_set.hypotheses
        if h.root_cause_category not in CHANGE_RELATED_CATEGORIES
    ]
    assert len(non_change) >= 1
    assert non_change[0].root_cause_category in {
        RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
        RootCauseCategory.RESOURCE_EXHAUSTION,
        RootCauseCategory.DATABASE_OUTAGE,
        RootCauseCategory.INFRASTRUCTURE_FAILURE,
    }


# ---------------------------------------------------------------------------
# HypothesisReviser Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_increments_revision_number(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    prev_hypotheses = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hyp_01",
                incident_id=sample_incident.incident_id,
                revision=1,
                statement="Database connection issue.",
                root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
                affected_component="checkout-api",
                supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="Timeout logged")],
                testable_prediction="Check DB.",
                status=HypothesisStatus.ACTIVE,
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    provider = FakeReasoningProvider(preset="deployment-regression")
    reviser = HypothesisReviser(provider=provider)

    revised_set = await reviser.revise(
        previous_hypotheses=prev_hypotheses,
        new_context=sample_context,
    )

    assert isinstance(revised_set, HypothesisSet)
    target = next(h for h in revised_set.hypotheses if h.hypothesis_id == "hyp_01")
    assert target.revision == 2


@pytest.mark.asyncio
async def test_revision_preserves_rejected_hypotheses(
    sample_incident: IncidentSeed,
    sample_context: IncidentContextSnapshot,
) -> None:
    # hyp_02 was already REJECTED in round 1
    prev_hypotheses = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hyp_01",
                incident_id=sample_incident.incident_id,
                revision=1,
                statement="Deployment configuration regression.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="checkout-api",
                supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="Errors after deploy")],
                testable_prediction="Check config.",
                status=HypothesisStatus.ACTIVE,
            ),
            Hypothesis(
                hypothesis_id="hyp_02",
                incident_id=sample_incident.incident_id,
                revision=1,
                statement="External gateway failure.",
                root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                affected_component="checkout-api",
                supporting_evidence=[],
                testable_prediction="Check gateway.",
                status=HypothesisStatus.REJECTED,
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    # Provider returns revision that attempts to omit hyp_02
    raw_revised = HypothesisSet(
        incident_id=sample_incident.incident_id,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hyp_01",
                incident_id=sample_incident.incident_id,
                revision=2,
                statement="Deployment configuration regression.",
                root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
                affected_component="checkout-api",
                supporting_evidence=[EvidenceCitation(evidence_id="ev_01", reason="Errors after deploy")],
                testable_prediction="Check config.",
                status=HypothesisStatus.ACTIVE,
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    provider = FakeReasoningProvider(custom_revised_hypotheses=raw_revised)
    reviser = HypothesisReviser(provider=provider)

    revised_set = await reviser.revise(
        previous_hypotheses=prev_hypotheses,
        new_context=sample_context,
    )

    # hyp_02 MUST be preserved and remain REJECTED
    hyp_ids = {h.hypothesis_id for h in revised_set.hypotheses}
    assert "hyp_02" in hyp_ids
    preserved_hyp_02 = next(h for h in revised_set.hypotheses if h.hypothesis_id == "hyp_02")
    assert preserved_hyp_02.status == HypothesisStatus.REJECTED
    assert preserved_hyp_02.revision == 2
