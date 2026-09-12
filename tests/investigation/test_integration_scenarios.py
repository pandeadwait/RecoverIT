"""
End-to-End Integration, Scenario, and Boundary Tests (Phase 10).

Verifies Person 3's components work together and validates all Person 3 acceptance criteria:
1. Scenario tests covering all 5 required families:
   - Scenario 1: Deployment configuration regression
   - Scenario 2: Memory / resource exhaustion
   - Scenario 3: Dependency incompatibility
   - Scenario 4: Actual database outage
   - Scenario 5: Coincidental deployment (external cause, deployment not the root cause)
2. Boundary contract tests:
   - EvidenceQueryPlan conforms and is consumable by Person 1
   - IncidentContextSnapshot is consumed correctly from Person 2
   - RankedHypothesisSet conforms to published schema and represents the system boundary
3. Integration verification & acceptance criteria:
   - System reports missing information
   - Every query valid against SourceCapabilityCatalog
   - >=2 hypotheses generated for suitable scenarios
   - Every hypothesis includes support + contradictions structures
   - Every citation resolves to real evidence in context
   - Final ranking is reproducible for identical inputs
   - Workflow terminates at RankedHypothesisSet; NO remediation paths exist
   - Seamless swapping between Fake, Recorded, and LLM providers without altering orchestration contracts
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InformationPriority,
    InvestigationState,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import StructuredError
from contracts.evidence.schemas import (
    EvidenceProvenance,
    EvidenceQuality,
    EvidenceRecord,
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
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    InvestigationBudget,
    MissingInformationAssessment,
)
from investigation.orchestration.orchestrator import (
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
    StoppingRuleEvaluator,
)
from reasoning.provider.fake_provider import FakeReasoningProvider
from reasoning.provider.recorded_provider import RecordedReasoningProvider
from tests.fixtures.person3.fixture_loader import (
    get_fixture_path,
    load_catalog,
    load_context_snapshot,
    load_incident_seed,
)


# ---------------------------------------------------------------------------
# Shared Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_catalog() -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id="inc_scen_all",
        generated_at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
        sources=[
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit", "start_time", "end_time"],
                maximum_window_seconds=3600,
                maximum_items=500,
            ),
            SourceCapability(
                source_type=SourceType.DEPLOYMENTS,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_window_seconds=86400,
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=True,
                supported_query_fields=["metric", "service", "limit"],
                maximum_window_seconds=3600,
                maximum_items=200,
            ),
            SourceCapability(
                source_type=SourceType.CHANGES,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_window_seconds=86400,
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.HEALTH,
                available=True,
                supported_query_fields=["service"],
                maximum_window_seconds=3600,
                maximum_items=10,
            ),
            SourceCapability(
                source_type=SourceType.PIPELINES,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_window_seconds=86400,
                maximum_items=20,
            ),
        ],
    )


@pytest.fixture
def base_budget() -> InvestigationBudget:
    return InvestigationBudget(
        max_rounds=3,
        max_queries=6,
        max_elapsed_seconds=300,
        max_reasoning_calls=8,
        max_input_units=20_000,
        max_output_units=5_000,
        minimum_hypotheses=2,
        maximum_hypotheses=5,
    )


# ---------------------------------------------------------------------------
# Part 1: All 5 Required Scenario Families
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_family_1_deployment_configuration_regression(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """
    Scenario 1: Deployment configuration regression.
    A new deployment updated database parameters with invalid configuration.
    """
    incident = IncidentSeed(
        incident_id="inc_scen_01",
        external_alert_id="alt_scen_01",
        service="payment-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 31, 0, tzinfo=timezone.utc),
        summary="Payment API HTTP 500 error spike following deployment v2.4.1",
    )

    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=base_catalog,
        budget=base_budget,
    )

    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert len(result.hypotheses) >= 2
    top = result.hypotheses[0]
    assert top.rank == 1
    assert top.root_cause_category == RootCauseCategory.CONFIGURATION_REGRESSION
    assert top.affected_component == "payment-api"
    assert top.evidence_score >= 40.0
    assert len(top.supporting_evidence) > 0


@pytest.mark.asyncio
async def test_scenario_family_2_memory_exhaustion(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """
    Scenario 2: Memory / Resource exhaustion.
    Memory leak led to pod restarts and OOM kills.
    """
    incident = IncidentSeed(
        incident_id="inc_scen_02",
        external_alert_id="alt_scen_02",
        service="worker-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 11, 1, 0, tzinfo=timezone.utc),
        summary="Worker pods repeatedly crashing with exit code 137 OOMKilled",
    )

    provider = FakeReasoningProvider(preset="resource-exhaustion")
    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=base_catalog,
        budget=base_budget,
    )

    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert len(result.hypotheses) >= 2
    top = result.hypotheses[0]
    assert top.rank == 1
    assert top.root_cause_category == RootCauseCategory.RESOURCE_EXHAUSTION
    assert "memory leak" in top.statement.lower()
    assert top.evidence_score >= 40.0


@pytest.mark.asyncio
async def test_scenario_family_3_dependency_incompatibility(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """
    Scenario 3: Dependency incompatibility.
    A transitive library upgrade introduced an incompatible function call / symbol error.
    """
    incident = IncidentSeed(
        incident_id="inc_scen_03",
        external_alert_id="alt_scen_03",
        service="billing-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 11, 30, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 11, 31, 0, tzinfo=timezone.utc),
        summary="ImportError / AttributeError on third-party client initialization",
    )

    provider = FakeReasoningProvider(preset="dependency-incompatibility")
    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=base_catalog,
        budget=base_budget,
    )

    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert len(result.hypotheses) >= 2
    top = result.hypotheses[0]
    assert top.rank == 1
    assert top.root_cause_category == RootCauseCategory.DEPENDENCY_INCOMPATIBILITY
    assert "dependency" in top.statement.lower() or "incompatible" in top.statement.lower()
    assert top.evidence_score >= 40.0


@pytest.mark.asyncio
async def test_scenario_family_4_actual_database_outage(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """
    Scenario 4: Actual database outage (not deployment related).
    Connection pool exhaustion and primary database failover.
    """
    incident = IncidentSeed(
        incident_id="inc_scen_04",
        external_alert_id="alt_scen_04",
        service="auth-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 12, 1, 0, tzinfo=timezone.utc),
        summary="Database cluster unreachable; pool exhausted across all replicas",
    )

    provider = FakeReasoningProvider(preset="database-outage")
    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=base_catalog,
        budget=base_budget,
    )

    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert len(result.hypotheses) >= 2
    top = result.hypotheses[0]
    assert top.rank == 1
    assert "connection pool" in top.statement.lower() or "database" in top.statement.lower()
    assert top.evidence_score >= 40.0


@pytest.mark.asyncio
async def test_scenario_family_5_coincidental_deployment(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """
    Scenario 5: Coincidental deployment.
    A deployment occurred at the same time, but the true root cause is an external service outage.
    The ranking engine must not jump to conclusion that the deployment is causal.
    """
    incident = IncidentSeed(
        incident_id="inc_scen_05",
        external_alert_id="alt_scen_05",
        service="checkout-frontend",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 12, 30, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 12, 31, 0, tzinfo=timezone.utc),
        summary="Checkout failures spiked simultaneously with frontend minor styling deploy",
    )

    provider = FakeReasoningProvider(preset="coincidental-deployment")
    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=base_catalog,
        budget=base_budget,
    )

    assert isinstance(result, RankedHypothesisSet)
    assert result.status == InvestigationStatus.COMPLETED
    assert len(result.hypotheses) >= 2
    top = result.hypotheses[0]
    assert top.rank == 1
    # Top hypothesis should be the true external dependency cause, not the deployment
    assert top.root_cause_category == RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE
    assert "external" in top.statement.lower() or "gateway" in top.statement.lower()


# ---------------------------------------------------------------------------
# Part 2: Boundary Contract Tests
# ---------------------------------------------------------------------------


def test_boundary_contract_evidence_query_plan_consumed_by_person_1(
    base_catalog: SourceCapabilityCatalog,
) -> None:
    """Verify Person 1 CollectionService consumes EvidenceQueryPlan according to schema."""
    plan = EvidenceQueryPlan(
        incident_id="inc_boundary_01",
        plan_id="plan_b_01",
        round=1,
        queries=[
            {
                "query_id": "qry_b_01",
                "source_type": SourceType.LOGS,
                "question": "Fetch error logs",
                "parameters": {"service": "payment-api", "limit": 100},
                "related_information_ids": ["need_01"],
                "expected_information_value": "high",
            }
        ],
    )

    # Person 1 executes plan and outputs RawEvidenceBatch
    collection_svc = InMemoryCollectionService()
    import asyncio
    batch = asyncio.run(collection_svc.execute(plan))

    assert isinstance(batch, RawEvidenceBatch)
    assert batch.incident_id == plan.incident_id
    assert batch.plan_id == plan.plan_id
    assert len(batch.results) == 1
    assert batch.results[0].query_id == "qry_b_01"
    assert batch.results[0].source_status == SourceStatus.OK


def test_boundary_contract_incident_context_snapshot_consumed_from_person_2() -> None:
    """Verify Person 2 ContextBuilder produces IncidentContextSnapshot that Person 3 consumes cleanly."""
    snapshot = IncidentContextSnapshot(
        incident_id="inc_boundary_02",
        snapshot_id="ctx_b_02",
        revision=2,
        created_at=datetime(2026, 9, 12, 13, 0, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service="order-service",
            environment="production",
            severity=Severity.CRITICAL,
            detected_at=datetime(2026, 9, 12, 12, 59, 0, tzinfo=timezone.utc),
            summary="Order failures",
        ),
        timeline=[],
        evidence=[
            EvidenceSummaryProjection(
                evidence_id="ev_b_100",
                source_type=SourceType.LOGS,
                evidence_type=EvidenceType.ERROR_EVENT,
                event_time=datetime(2026, 9, 12, 13, 0, 0, tzinfo=timezone.utc),
                summary="NullPointerException in handler",
                quality=EvidenceQuality(reliability=Reliability.HIGH),
            )
        ],
        source_coverage={SourceType.LOGS: SourceCoverageStatus.AVAILABLE},
    )

    # Person 3 consumes snapshot directly
    provider = FakeReasoningProvider()
    import asyncio
    mia = asyncio.run(
        provider.assess_missing_information(
            incident=IncidentSeed(
                incident_id="inc_boundary_02",
                external_alert_id="alt_02",
                service="order-service",
                environment="production",
                severity=Severity.CRITICAL,
                detected_at=datetime(2026, 9, 12, 12, 59, 0, tzinfo=timezone.utc),
                received_at=datetime(2026, 9, 12, 13, 0, 0, tzinfo=timezone.utc),
                summary="Order failures",
            ),
            source_capabilities=SourceCapabilityCatalog(
                incident_id="inc_boundary_02",
                generated_at=datetime(2026, 9, 12, 13, 0, 0, tzinfo=timezone.utc),
                sources=[],
            ),
            context=snapshot,
            active_hypotheses=[],
        )
    )

    assert isinstance(mia, MissingInformationAssessment)
    assert mia.incident_id == "inc_boundary_02"
    assert "ev_b_100" in mia.known_facts[0].evidence_ids


def test_boundary_contract_ranked_hypothesis_set_published_schema() -> None:
    """Verify RankedHypothesisSet conforms strictly to WORK_DIVISION §8.6."""
    ranked = RankedHypothesisSet(
        incident_id="inc_boundary_03",
        context_snapshot_id="ctx_b_03",
        ranking_id="rank_b_03",
        created_at=datetime(2026, 9, 12, 13, 15, 0, tzinfo=timezone.utc),
        status=InvestigationStatus.COMPLETED,
        hypotheses=[],
        remaining_uncertainty=["Minor uncertainty in queue depth"],
        budget_usage=BudgetUsage(),
    )

    json_str = ranked.model_dump_json()
    assert '"schema_version":"1.0"' in json_str or '"schema_version": "1.0"' in json_str
    parsed = RankedHypothesisSet.model_validate_json(json_str)
    assert parsed.ranking_id == "rank_b_03"
    assert parsed.status == InvestigationStatus.COMPLETED


# ---------------------------------------------------------------------------
# Part 3: All Person 3 Acceptance Criteria Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_criteria_system_reports_missing_information(
    base_catalog: SourceCapabilityCatalog,
) -> None:
    """Acceptance criterion 1: System reports missing information."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")

    mia = await provider.assess_missing_information(
        incident=incident,
        source_capabilities=base_catalog,
        context=context,
        active_hypotheses=[],
    )

    assert len(mia.missing_information) > 0
    assert mia.missing_information[0].question != ""
    assert mia.missing_information[0].priority in {
        InformationPriority.HIGH,
        InformationPriority.MEDIUM,
        InformationPriority.LOW,
    }


@pytest.mark.asyncio
async def test_acceptance_criteria_every_query_valid_against_catalog(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """Acceptance criterion 2: Every query valid against SourceCapabilityCatalog."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")

    mia = await provider.assess_missing_information(incident, base_catalog, context, [])
    plan = await provider.plan_queries(mia, base_catalog, context, base_budget)

    avail_map = {s.source_type: s for s in base_catalog.sources if s.available}
    for q in plan.queries:
        assert q.source_type in avail_map
        cap = avail_map[q.source_type]
        for field in q.parameters:
            assert field in cap.supported_query_fields


@pytest.mark.asyncio
async def test_acceptance_criteria_minimum_hypotheses_generated(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """Acceptance criterion 3: >= 2 hypotheses for suitable scenarios."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")

    hypotheses = await provider.generate_hypotheses(incident, context, base_budget)
    assert len(hypotheses.hypotheses) >= 2


@pytest.mark.asyncio
async def test_acceptance_criteria_every_hypothesis_includes_support_and_contradictions(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """Acceptance criterion 4: Every hypothesis includes support + contradictions structures."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")

    hypotheses = await provider.generate_hypotheses(incident, context, base_budget)
    for h in hypotheses.hypotheses:
        assert isinstance(h.supporting_evidence, list)
        assert isinstance(h.contradicting_evidence, list)


@pytest.mark.asyncio
async def test_acceptance_criteria_every_citation_resolves_to_evidence(
    base_catalog: SourceCapabilityCatalog,
    base_budget: InvestigationBudget,
) -> None:
    """Acceptance criterion 5: Every citation resolves to real evidence in context."""
    provider = FakeReasoningProvider(preset="deployment-regression")
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")

    hypotheses = await provider.generate_hypotheses(incident, context, base_budget)
    context_eids = {e.evidence_id for e in context.evidence}

    for h in hypotheses.hypotheses:
        for citation in h.supporting_evidence:
            assert citation.evidence_id in context_eids
        for citation in h.contradicting_evidence:
            assert citation.evidence_id in context_eids


@pytest.mark.asyncio
async def test_acceptance_criteria_final_ranking_is_reproducible_identical_inputs() -> None:
    """Acceptance criterion 6: Final ranking is reproducible for identical inputs."""
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_deployment_regression.json")

    # Run pass 1
    p1 = RecordedReasoningProvider.from_file(fixture_path)
    orch_1 = InvestigationOrchestrator(
        provider=p1,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )
    result_1 = await orch_1.run(incident=incident, source_capabilities=catalog)

    # Run pass 2
    p2 = RecordedReasoningProvider.from_file(fixture_path)
    orch_2 = InvestigationOrchestrator(
        provider=p2,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )
    result_2 = await orch_2.run(incident=incident, source_capabilities=catalog)

    assert result_1 == result_2
    assert result_1.model_dump_json() == result_2.model_dump_json()


def test_acceptance_criteria_workflow_stops_at_ranked_hypotheses_and_no_remediation_exists() -> None:
    """Acceptance criterion 7 & 8: Workflow stops at RankedHypothesisSet; NO remediation paths exist."""
    orchestrator = InvestigationOrchestrator(
        provider=FakeReasoningProvider(),
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(),
    )

    # Verify return type annotation of run is RankedHypothesisSet
    import inspect
    sig = inspect.signature(orchestrator.run)
    assert sig.return_annotation is RankedHypothesisSet or "RankedHypothesisSet" in str(sig.return_annotation)

    # Verify no remediation methods exist on orchestrator
    remediation_keywords = ["remediate", "rollback", "restart", "execute_action", "apply_fix", "heal"]
    for method_name in dir(orchestrator):
        for kw in remediation_keywords:
            assert kw not in method_name.lower(), f"Remediation method '{method_name}' found on orchestrator!"


@pytest.mark.asyncio
async def test_acceptance_criteria_replacing_provider_does_not_change_contracts(
    base_catalog: SourceCapabilityCatalog,
) -> None:
    """Acceptance criterion 9: Replacing ReasoningProvider doesn't change orchestration contracts."""
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_deployment_regression.json")

    fake_prov = FakeReasoningProvider(preset="deployment-regression")
    rec_prov = RecordedReasoningProvider.from_file(fixture_path)

    orch_fake = InvestigationOrchestrator(
        provider=fake_prov,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )
    orch_rec = InvestigationOrchestrator(
        provider=rec_prov,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    res_fake = await orch_fake.run(incident=incident, source_capabilities=catalog)
    res_rec = await orch_rec.run(incident=incident, source_capabilities=catalog)

    # Both produce valid RankedHypothesisSet conforming strictly to contract
    assert isinstance(res_fake, RankedHypothesisSet)
    assert isinstance(res_rec, RankedHypothesisSet)
    assert res_fake.status == InvestigationStatus.COMPLETED
    assert res_rec.status == InvestigationStatus.COMPLETED
    assert res_fake.schema_version == "1.0"
    assert res_rec.schema_version == "1.0"
