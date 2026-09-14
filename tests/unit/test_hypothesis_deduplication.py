"""
Unit tests for HypothesisDeduplicator and integration with generator/reviser/orchestrator.

Verifies:
- Text normalization, domain synonym mapping, and basic stemming.
- Hybrid containment and Jaccard similarity computation.
- Category, component, and status isolation (never merge different categories or components).
- Merging logic: longer statement, role promotion (CAUSE > CORRELATION), gap union.
- In-place pairwise deduplication on hypothesis sets.
- Integration in HypothesisGenerator, HypothesisReviser, and InvestigationOrchestrator.

Phase 5 Acceptance Criteria from CREDIBILITY_IMPROVEMENT_PLAN.md:
- Prevent multiple ranks from presenting the same explanation with different wording.
- Preserve genuinely distinct alternative explanations.
- Guarantee no two ranked hypotheses are semantic duplicates.
"""

from datetime import datetime, timezone
import pytest

from contracts.common import (
    EvidenceRole,
    HypothesisStatus,
    RootCauseCategory,
    SourceType,
    EvidenceType,
    Reliability,
    Severity,
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
from reasoning.hypotheses.deduplicator import HypothesisDeduplicator
from reasoning.hypotheses.generator import HypothesisGenerator
from reasoning.hypotheses.reviser import HypothesisReviser
from reasoning.provider.interface import ReasoningProvider


def _dummy_evidence(ev_id: str, summary: str = "Test record") -> EvidenceSummaryProjection:
    return EvidenceSummaryProjection(
        evidence_id=ev_id,
        source_type=SourceType.CONFIGURATION,
        evidence_type=EvidenceType.CONFIGURATION_CHANGE,
        event_time=datetime.now(timezone.utc),
        summary=summary,
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )


def _dummy_context(evidence_ids: list[str]) -> IncidentContextSnapshot:
    now = datetime.now(timezone.utc)
    return IncidentContextSnapshot(
        snapshot_id="ctx_test_1",
        incident_id="inc_test_1",
        revision=1,
        created_at=now,
        incident=IncidentSummary(
            service="order-service",
            environment="production",
            severity=Severity.CRITICAL,
            detected_at=now,
            summary="Test incident",
        ),
        evidence=[_dummy_evidence(eid) for eid in evidence_ids],
        timeline=[],
        relationships=[],
        source_coverage={},
        warnings=[],
    )


def _dummy_incident() -> IncidentSeed:
    now = datetime.now(timezone.utc)
    return IncidentSeed(
        incident_id="inc_test_1",
        external_alert_id="alert_1",
        service="order-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=now,
        received_at=now,
        summary="High latency on order-service: Database connections failing",
    )


# ===========================================================================
# 1. Normalization & Similarity Tests
# ===========================================================================

def test_normalize_text_synonyms_and_stemming():
    dedup = HypothesisDeduplicator()
    t1 = "There is a misconfiguration in the database service connection."
    norm1 = dedup.normalize_text(t1)
    
    # Expected synonym mappings:
    # misconfiguration -> config
    # database -> db
    # service -> svc
    # connection -> connect
    assert "config" in norm1
    assert "db" in norm1
    assert "svc" in norm1
    assert "connect" in norm1
    # Stopwords like "there", "is", "a", "in", "the" should be stripped
    assert "there" not in norm1
    assert "the" not in norm1


def test_compute_similarity_paraphrase_vs_distinct():
    dedup = HypothesisDeduplicator()
    s1 = "The database connection configuration is incorrect."
    s2 = "There is a misconfiguration in the database service connection."
    s3 = "External payment gateway is experiencing an outage and returning 504 timeouts."

    sim_para = dedup.compute_similarity(s1, s2)
    sim_diff = dedup.compute_similarity(s1, s3)

    assert sim_para > 0.50, f"Expected high similarity for paraphrases, got {sim_para}"
    assert sim_diff < 0.20, f"Expected low similarity for distinct texts, got {sim_diff}"


# ===========================================================================
# 2. Duplicate Detection Rules
# ===========================================================================

def test_are_duplicates_same_category_component_and_semantics():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection pool size is misconfigured.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_cfg_1", reason="Pool size dropped to 2")],
        contradicting_evidence=[],
        missing_information_ids=["gap_1"],
        testable_prediction="Check config commit",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="The database connection pool configuration is set incorrectly.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order_service_db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_cfg_1", reason="Config change")],
        contradicting_evidence=[],
        missing_information_ids=["gap_2"],
        testable_prediction="Inspect git history",
        status=HypothesisStatus.ACTIVE,
    )

    assert dedup.are_duplicates(h1, h2) is True


def test_are_duplicates_different_categories_never_merge():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection failure due to misconfiguration.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Verify config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection failure due to underlying database node outage.",
        root_cause_category=RootCauseCategory.DATABASE_OUTAGE,
        affected_component="order-service-db",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Verify DB health",
        status=HypothesisStatus.ACTIVE,
    )

    assert dedup.are_duplicates(h1, h2) is False


def test_are_duplicates_different_components_never_merge():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Configuration regression caused service disruption.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="Configuration regression caused service disruption.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="payment-service",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )

    assert dedup.are_duplicates(h1, h2) is False


def test_are_duplicates_rejected_never_merges_with_active():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection pool configuration is incorrect.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection pool configuration is incorrect.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.REJECTED,
    )

    assert dedup.are_duplicates(h1, h2) is False


# ===========================================================================
# 3. Merging Logic
# ===========================================================================

def test_merge_hypotheses_preserves_best_content_and_promotes_roles():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Short statement about DB config.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_cfg_1",
                reason="Short reason",
                role=EvidenceRole.CORRELATION,
            ),
            EvidenceCitation(
                evidence_id="ev_log_1",
                reason="Log showing connection timeout",
                role=EvidenceRole.EFFECT,
            ),
        ],
        contradicting_evidence=[],
        missing_information_ids=["gap_1"],
        testable_prediction="Short prediction",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=2,
        statement="Comprehensive and detailed explanation showing DB connection pool exhaustion from commit config.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[
            EvidenceCitation(
                evidence_id="ev_cfg_1",
                reason="Detailed git commit reducing max_connections from 100 to 2 directly causing pool exhaustion",
                role=EvidenceRole.CAUSE,  # Stronger role
            ),
            EvidenceCitation(
                evidence_id="ev_metric_1",
                reason="Active connections plateaued at 2",
                role=EvidenceRole.EFFECT,
            ),
        ],
        contradicting_evidence=[
            EvidenceCitation(
                evidence_id="ev_contra_1",
                reason="Baseline CPU remained low",
                role=EvidenceRole.CONTRADICTION,
            )
        ],
        missing_information_ids=["gap_1", "gap_2"],
        testable_prediction="Detailed testable prediction validating connection metrics against pool limits",
        status=HypothesisStatus.ACTIVE,
    )

    merged = dedup.merge_hypotheses(h1, h2)

    # Preferred statement is the longer, more specific one
    assert merged.statement == h2.statement
    assert merged.testable_prediction == h2.testable_prediction

    # Canonical ID is min ("hyp_001"), revision is max (2)
    assert merged.hypothesis_id == "hyp_001"
    assert merged.revision == 2

    # Evidence citations are merged uniquely
    sup_ids = {c.evidence_id for c in merged.supporting_evidence}
    assert sup_ids == {"ev_cfg_1", "ev_log_1", "ev_metric_1"}

    # ev_cfg_1 had CORRELATION in h1 but CAUSE in h2 -> role must be CAUSE
    cfg_cit = next(c for c in merged.supporting_evidence if c.evidence_id == "ev_cfg_1")
    assert cfg_cit.role == EvidenceRole.CAUSE
    assert cfg_cit.reason == h2.supporting_evidence[0].reason

    # Contradicting evidence preserved
    assert len(merged.contradicting_evidence) == 1
    assert merged.contradicting_evidence[0].evidence_id == "ev_contra_1"

    # Missing information IDs merged uniquely
    assert merged.missing_information_ids == ["gap_1", "gap_2"]


def test_deduplicate_list_preserves_distinct_and_merges_duplicates():
    dedup = HypothesisDeduplicator()
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection configuration is incorrect.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change")],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Verify config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="There is a misconfiguration in the database service connection.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_2", reason="Log failure")],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Verify logs",
        status=HypothesisStatus.ACTIVE,
    )
    h3 = Hypothesis(
        hypothesis_id="hyp_003",
        incident_id="inc_test_1",
        revision=1,
        statement="Downstream payment gateway latency is causing thread exhaustion.",
        root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
        affected_component="payment-gateway",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_3", reason="Gateway timeout")],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check gateway health",
        status=HypothesisStatus.ACTIVE,
    )

    result = dedup.deduplicate([h1, h2, h3])

    # h1 and h2 should merge, h3 remains distinct
    assert len(result) == 2
    merged_ids = {h.hypothesis_id for h in result}
    assert "hyp_001" in merged_ids
    assert "hyp_003" in merged_ids

    # Merged hypothesis has both citations
    merged_h = next(h for h in result if h.hypothesis_id == "hyp_001")
    merged_ev_ids = {c.evidence_id for c in merged_h.supporting_evidence}
    assert merged_ev_ids == {"ev_1", "ev_2"}


# ===========================================================================
# 4. Integration with HypothesisGenerator & Reviser
# ===========================================================================

class FakeReasoningProvider(ReasoningProvider):
    def __init__(self, hypotheses: list[Hypothesis]):
        self._hypotheses = hypotheses

    async def generate_hypotheses(self, incident, context, limits):
        return HypothesisSet(
            incident_id=incident.incident_id,
            hypotheses=self._hypotheses,
            generated_at=datetime.now(timezone.utc),
        )

    async def revise_hypotheses(self, previous_hypotheses, new_context):
        return HypothesisSet(
            incident_id=previous_hypotheses.incident_id,
            hypotheses=self._hypotheses,
            generated_at=datetime.now(timezone.utc),
        )

    async def assess_missing_information(self, *args, **kwargs):
        raise NotImplementedError

    async def plan_evidence_queries(self, *args, **kwargs):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_hypothesis_generator_deduplicates_output():
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection configuration is incorrect.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change", role=EvidenceRole.CAUSE)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="There is a misconfiguration in the database service connection.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change", role=EvidenceRole.CAUSE)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )

    provider = FakeReasoningProvider([h1, h2])
    generator = HypothesisGenerator(provider=provider)
    context = _dummy_context(["ev_1"])
    limits = InvestigationBudget(maximum_hypotheses=5)

    result_set = await generator.generate(_dummy_incident(), context, limits)

    # In addition to deduplication, generator ensures at least 1 non-change hypothesis if all were change-related
    # The two change-related duplicates should merge into 1, plus 1 non-change alternative -> total 2
    change_hypotheses = [
        h for h in result_set.hypotheses
        if h.root_cause_category == RootCauseCategory.CONFIGURATION_REGRESSION
    ]
    assert len(change_hypotheses) == 1
    assert change_hypotheses[0].hypothesis_id == "hyp_001"


@pytest.mark.asyncio
async def test_hypothesis_reviser_deduplicates_output():
    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection pool size was misconfigured.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change", role=EvidenceRole.CAUSE)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection pool configuration is set incorrectly.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change", role=EvidenceRole.CAUSE)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )

    provider = FakeReasoningProvider([h1, h2])
    reviser = HypothesisReviser(provider=provider)
    context = _dummy_context(["ev_1"])
    prev_set = HypothesisSet(
        incident_id="inc_test_1",
        hypotheses=[h1],
        generated_at=datetime.now(timezone.utc),
    )

    revised_set = await reviser.revise(prev_set, context)

    # Should deduplicate h1 and h2 into a single active hypothesis
    assert len(revised_set.hypotheses) == 1
    assert revised_set.hypotheses[0].hypothesis_id == "hyp_001"


def test_orchestrator_prepare_hypotheses_deduplicates_before_ranking():
    from investigation.orchestration.orchestrator import (
        InMemoryCollectionService,
        InMemoryContextBuilder,
        InvestigationOrchestrator,
    )

    h1 = Hypothesis(
        hypothesis_id="hyp_001",
        incident_id="inc_test_1",
        revision=1,
        statement="Database connection configuration is incorrect.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_1", reason="Config change", role=EvidenceRole.CAUSE)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )
    h2 = Hypothesis(
        hypothesis_id="hyp_002",
        incident_id="inc_test_1",
        revision=1,
        statement="There is a misconfiguration in the database service connection.",
        root_cause_category=RootCauseCategory.CONFIGURATION_REGRESSION,
        affected_component="order-service-db",
        supporting_evidence=[EvidenceCitation(evidence_id="ev_2", reason="Log failure", role=EvidenceRole.EFFECT)],
        contradicting_evidence=[],
        missing_information_ids=[],
        testable_prediction="Check config",
        status=HypothesisStatus.ACTIVE,
    )

    hyp_set = HypothesisSet(
        incident_id="inc_test_1",
        hypotheses=[h1, h2],
        generated_at=datetime.now(timezone.utc),
    )

    provider = FakeReasoningProvider([h1, h2])
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(),
    )

    prepared = orchestrator._prepare_hypotheses_for_ranking(hyp_set)
    assert prepared is not None
    assert len(prepared.hypotheses) == 1
    assert prepared.hypotheses[0].hypothesis_id == "hyp_001"
    assert len(prepared.hypotheses[0].supporting_evidence) == 2

