"""
Unit tests for Phase 3: Evidence Planning Improvements.

Verifies:
- InformationGapCategory enum taxonomy.
- MissingInformationItem schema and default category.
- MissingInformationAssessor multi-round carryover of unresolved HIGH priority gaps.
- EvidenceQueryPlanner question neutrality checking.
- EvidenceQueryPlanner gap linking (auto-populating related_information_ids).
- EvidenceQueryPlanner direct causal query prioritization.
- FakeReasoningProvider preset categories.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog
from contracts.common import (
    InformationGapCategory,
    InformationPriority,
    InformationValueLevel,
    Severity,
    SourceType,
)
from contracts.evidence.schemas import (
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.missing_information.assessor import MissingInformationAssessor
from investigation.query_planning.planner import EvidenceQueryPlanner
from reasoning.provider.fake_provider import FakeReasoningProvider


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_plan_p3_01",
        external_alert_id="alt_ext_01",
        service="payment-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="Payment failure rate spike",
        labels={"tier": "1"},
    )


@pytest.fixture
def sample_catalog(sample_incident: IncidentSeed) -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        sources=[
            SourceCapability(
                source_type=SourceType.DEPLOYMENTS,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.CHANGES,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit"],
                maximum_items=100,
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=True,
                supported_query_fields=["metric", "service", "limit"],
                maximum_items=100,
            ),
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_p3_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            summary=sample_incident.summary,
            detected_at=sample_incident.detected_at,
        ),
        evidence=[],
    )


def test_information_gap_category_enum():
    """Verify all four required taxonomy categories exist with expected values."""
    assert InformationGapCategory.SYMPTOM_CONFIRMATION == "symptom_confirmation"
    assert InformationGapCategory.TEMPORAL_CORRELATION == "temporal_correlation"
    assert InformationGapCategory.DIRECT_CAUSAL_EVIDENCE == "direct_causal_evidence"
    assert InformationGapCategory.CONTRADICTING_EVIDENCE == "contradicting_evidence"


def test_missing_information_item_category_default_and_explicit():
    """Verify MissingInformationItem defaults category to DIRECT_CAUSAL_EVIDENCE and accepts explicit category."""
    item_default = MissingInformationItem(
        information_id="gap_01",
        question="Were there recent config changes?",
        reason="Check for regression",
        priority=InformationPriority.HIGH,
        candidate_sources=[SourceType.CHANGES],
    )
    assert item_default.category == InformationGapCategory.DIRECT_CAUSAL_EVIDENCE

    item_explicit = MissingInformationItem(
        information_id="gap_02",
        question="Did third party payment API report an outage?",
        reason="Differentiate internal regression from vendor outage",
        priority=InformationPriority.HIGH,
        candidate_sources=[SourceType.LOGS],
        category=InformationGapCategory.CONTRADICTING_EVIDENCE,
    )
    assert item_explicit.category == InformationGapCategory.CONTRADICTING_EVIDENCE

    # JSON roundtrip
    dumped = item_explicit.model_dump_json()
    loaded = MissingInformationItem.model_validate_json(dumped)
    assert loaded.category == InformationGapCategory.CONTRADICTING_EVIDENCE


def test_missing_information_assessor_preserves_category(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """Assessor preserves category during validation."""
    custom_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_custom",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_sym",
                question="What is the 500 error count?",
                reason="Confirm symptom scale",
                priority=InformationPriority.MEDIUM,
                candidate_sources=[SourceType.METRICS],
                category=InformationGapCategory.SYMPTOM_CONFIRMATION,
            ),
            MissingInformationItem(
                information_id="gap_temp",
                question="When did error rates cross baseline?",
                reason="Correlate timeline",
                priority=InformationPriority.LOW,
                candidate_sources=[SourceType.METRICS],
                category=InformationGapCategory.TEMPORAL_CORRELATION,
            ),
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=custom_assessment)
    assessor = MissingInformationAssessor(provider=provider)
    validated = assessor.validate_assessment(
        assessment=custom_assessment,
        source_capabilities=sample_catalog,
        context=sample_context,
    )

    assert len(validated.missing_information) == 2
    assert validated.missing_information[0].category == InformationGapCategory.SYMPTOM_CONFIRMATION
    assert validated.missing_information[1].category == InformationGapCategory.TEMPORAL_CORRELATION


def test_missing_information_assessor_carries_forward_high_priority_gaps(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """Unresolved HIGH priority gaps carry forward across rounds."""
    round1_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_r1",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_causal_high",
                question="Was a configuration change deployed before the incident?",
                reason="Identify causal change",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.CHANGES],
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                resolved=False,
            ),
            MissingInformationItem(
                information_id="gap_low",
                question="What was CPU utilization?",
                reason="Background metric",
                priority=InformationPriority.LOW,
                candidate_sources=[SourceType.METRICS],
                category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                resolved=False,
            ),
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    # Round 2 assessment from provider only discovers a new medium gap
    round2_raw = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_r2",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_new_med",
                question="Are there error logs on the checkout handler?",
                reason="Inspect stack traces",
                priority=InformationPriority.MEDIUM,
                candidate_sources=[SourceType.LOGS],
                category=InformationGapCategory.SYMPTOM_CONFIRMATION,
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=round2_raw)
    assessor = MissingInformationAssessor(provider=provider)

    validated_r2 = assessor.validate_assessment(
        assessment=round2_raw,
        source_capabilities=sample_catalog,
        context=sample_context,
        previous_assessment=round1_assessment,
    )

    gap_ids = [item.information_id for item in validated_r2.missing_information]
    # gap_causal_high should be carried forward because it is HIGH priority and unresolved
    assert "gap_causal_high" in gap_ids
    # gap_low should NOT be carried forward because it is LOW priority
    assert "gap_low" not in gap_ids
    # gap_new_med should be present
    assert "gap_new_med" in gap_ids


def test_missing_information_assessor_does_not_duplicate_carried_gaps(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """If provider re-identifies the same HIGH gap, it is not duplicated."""
    round1_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_r1",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_causal_high",
                question="Was a configuration change deployed before the incident?",
                reason="Identify causal change",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.CHANGES],
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    round2_raw = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_r2",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_causal_high",
                question="Was a configuration change deployed before the incident?",
                reason="Identify causal change",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.CHANGES],
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=round2_raw)
    assessor = MissingInformationAssessor(provider=provider)

    validated = assessor.validate_assessment(
        assessment=round2_raw,
        source_capabilities=sample_catalog,
        context=sample_context,
        previous_assessment=round1_assessment,
    )

    assert len(validated.missing_information) == 1


def test_evidence_query_planner_question_neutrality():
    """EvidenceQueryPlanner.check_question_neutrality distinguishes neutral vs blame-presuming questions."""
    # Presumptive / leading questions
    assert not EvidenceQueryPlanner.check_question_neutrality("Which deployment broke the service?")[0]
    assert not EvidenceQueryPlanner.check_question_neutrality("Who broke the database connection pool?")[0]
    assert not EvidenceQueryPlanner.check_question_neutrality("Find the faulty deployment in the log")[0]
    assert not EvidenceQueryPlanner.check_question_neutrality("What bad configuration introduced by the team?")[0]
    assert not EvidenceQueryPlanner.check_question_neutrality("Identify the culprit deployment")[0]

    # Neutral questions
    assert EvidenceQueryPlanner.check_question_neutrality(
        "Were any relevant configuration or code changes introduced before the alert?"
    )[0]
    assert EvidenceQueryPlanner.check_question_neutrality(
        "What deployments completed in the 60 minutes preceding the incident?"
    )[0]
    assert EvidenceQueryPlanner.check_question_neutrality(
        "Is there an elevated 500 error rate in the service logs?"
    )[0]
    assert EvidenceQueryPlanner.check_question_neutrality(
        "Did memory usage exceed container limits?"
    )[0]


def test_evidence_query_planner_populates_related_information_ids(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """EvidenceQueryPlanner auto-links query to related_information_ids if empty based on candidate_sources."""
    missing = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_deploy_info",
                question="Were deployments executed recently?",
                reason="Check for change regression",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.DEPLOYMENTS],
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    raw_plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan_01",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_dep_01",
                source_type=SourceType.DEPLOYMENTS,
                question="What deployments were executed in the last 2 hours?",
                parameters={"service": "payment-api", "limit": 10},
                related_information_ids=[],  # Left empty by provider
                expected_information_value=InformationValueLevel.HIGH,
            )
        ],
    )

    planner = EvidenceQueryPlanner(provider=FakeReasoningProvider())
    validated = planner.validate_plan(
        plan=raw_plan,
        source_capabilities=sample_catalog,
        budget=InvestigationBudget(max_queries=5, max_rounds=2),
        round_num=1,
        missing_information=missing,
    )

    assert len(validated.queries) == 1
    # Check that related_information_ids was automatically populated with gap_deploy_info
    assert "gap_deploy_info" in validated.queries[0].related_information_ids


def test_evidence_query_planner_prioritizes_direct_causal_sources(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """When direct causal gaps exist, queries targeting causal sources (CHANGES, DEPLOYMENTS) are sorted first."""
    missing = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="gap_config_change",
                question="What config changes were committed?",
                reason="Identify causal change",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.CHANGES],
                category=InformationGapCategory.DIRECT_CAUSAL_EVIDENCE,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    # Provider returns a LOGS query first, then a CHANGES query
    raw_plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan_01",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_log_01",
                source_type=SourceType.LOGS,
                question="What are recent error messages?",
                parameters={"service": "payment-api", "severity": "error", "limit": 10},
                related_information_ids=[],
                expected_information_value=InformationValueLevel.MEDIUM,
            ),
            EvidenceQueryPlanQuery(
                query_id="qry_change_01",
                source_type=SourceType.CHANGES,
                question="What configuration changes were introduced recently?",
                parameters={"service": "payment-api", "limit": 10},
                related_information_ids=["gap_config_change"],
                expected_information_value=InformationValueLevel.HIGH,
            ),
        ],
    )

    planner = EvidenceQueryPlanner(provider=FakeReasoningProvider())
    validated = planner.validate_plan(
        plan=raw_plan,
        source_capabilities=sample_catalog,
        budget=InvestigationBudget(max_queries=5, max_rounds=2),
        round_num=1,
        missing_information=missing,
    )

    assert len(validated.queries) == 2
    # The CHANGES query should be sorted first because a DIRECT_CAUSAL_EVIDENCE gap exists
    assert validated.queries[0].source_type == SourceType.CHANGES
    assert validated.queries[1].source_type == SourceType.LOGS


@pytest.mark.asyncio
async def test_fake_provider_presets_populate_categories(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """Verify all FakeReasoningProvider presets provide category in assess_missing_information."""
    presets = [
        "database-outage",
        "resource-exhaustion",
        "dependency-incompatibility",
        "coincidental-deployment",
        "deployment-regression",
    ]

    for p in presets:
        provider = FakeReasoningProvider(preset=p)
        assessment = await provider.assess_missing_information(
            incident=sample_incident,
            source_capabilities=sample_catalog,
            context=sample_context,
            active_hypotheses=[],
        )
        assert len(assessment.missing_information) >= 1
        for item in assessment.missing_information:
            assert isinstance(item.category, (InformationGapCategory, str))
            assert item.category in {c.value for c in InformationGapCategory}


@pytest.mark.asyncio
async def test_orchestrator_multi_round_missing_info_tracking(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
):
    """Verify InvestigationOrchestrator tracks last_missing_info and exposes it."""
    from investigation.orchestration.orchestrator import (
        InMemoryCollectionService,
        InMemoryContextBuilder,
        InvestigationOrchestrator,
        StoppingRuleEvaluator,
    )
    from investigation.orchestration.state_machine import InMemoryCheckpointStore

    provider = FakeReasoningProvider(preset="deployment-regression")
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=sample_context.evidence),
        checkpoint_store=InMemoryCheckpointStore(),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    budget = InvestigationBudget(max_rounds=2, max_queries=5, max_reasoning_calls=10)
    assert orchestrator.last_missing_info is None

    result = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        budget=budget,
    )

    assert orchestrator.last_missing_info is not None
    assert isinstance(orchestrator.last_missing_info, MissingInformationAssessment)
    assert len(orchestrator.last_missing_info.missing_information) >= 1

