"""
Replay and Fixture Integration Tests (Phase 8).

Verifies:
- Full recorded runs are replayable without a live model across 3 distinct scenarios:
  1. Deployment configuration regression
  2. Database outage (not deployment-related)
  3. Resource exhaustion
- Replay produces 100% identical output across repeated runs (deterministic replayability).
- Unavailable sources produce uncertainty rather than invented facts.
- Provider replacement (Fake vs Recorded) passes identical contract tests without altering orchestration contracts.

See WORK_DIVISION.md §8.8, §8.9 and ARCHITECTURE.md §8, §9.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.common import (
    ConfidenceLabel,
    InvestigationState,
    InvestigationStatus,
    SourceCoverageStatus,
    SourceStatus,
    StopReason,
)
from contracts.hypothesis.schemas import RankedHypothesisSet
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
# Scenario 1: Deployment Configuration Regression Replay Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_deployment_regression_scenario() -> None:
    """Verify deterministic replay for deployment configuration regression scenario."""
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_deployment_regression.json")

    # Provider 1 & 2 for replayability check
    provider_1 = RecordedReasoningProvider.from_file(fixture_path)
    provider_2 = RecordedReasoningProvider.from_file(fixture_path)

    collection_1 = InMemoryCollectionService()
    context_builder_1 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_1 = InvestigationOrchestrator(
        provider=provider_1,
        collection_service=collection_1,
        context_builder=context_builder_1,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_1 = await orchestrator_1.run(
        incident=incident,
        source_capabilities=catalog,
    )

    # Output verification
    assert isinstance(result_1, RankedHypothesisSet)
    assert result_1.status == InvestigationStatus.COMPLETED
    assert len(result_1.hypotheses) >= 1
    assert result_1.hypotheses[0].hypothesis_id == "hyp_deploy_01"
    assert (
        result_1.hypotheses[0].statement
        == "Deployment v2.4.1 introduced an invalid database connection pool sizing configuration."
    )
    assert result_1.hypotheses[0].evidence_score > 50.0

    # Repeat run for deterministic equality
    collection_2 = InMemoryCollectionService()
    context_builder_2 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_2 = InvestigationOrchestrator(
        provider=provider_2,
        collection_service=collection_2,
        context_builder=context_builder_2,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_2 = await orchestrator_2.run(
        incident=incident,
        source_capabilities=catalog,
    )

    # Identical output check across runs
    assert result_1 == result_2


# ---------------------------------------------------------------------------
# Scenario 2: Database Outage Replay Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_database_outage_scenario() -> None:
    """Verify deterministic replay for database outage scenario."""
    incident = load_incident_seed("incident_database_outage.json")
    context = load_context_snapshot("context_database_outage.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_database_outage.json")

    provider_1 = RecordedReasoningProvider.from_file(fixture_path)
    provider_2 = RecordedReasoningProvider.from_file(fixture_path)

    collection_1 = InMemoryCollectionService()
    context_builder_1 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_1 = InvestigationOrchestrator(
        provider=provider_1,
        collection_service=collection_1,
        context_builder=context_builder_1,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_1 = await orchestrator_1.run(
        incident=incident,
        source_capabilities=catalog,
    )

    assert result_1.status == InvestigationStatus.COMPLETED
    assert len(result_1.hypotheses) >= 1
    assert result_1.hypotheses[0].hypothesis_id == "hyp_db_01"
    assert "pg-db-01" in result_1.hypotheses[0].statement

    # Replay check
    collection_2 = InMemoryCollectionService()
    context_builder_2 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_2 = InvestigationOrchestrator(
        provider=provider_2,
        collection_service=collection_2,
        context_builder=context_builder_2,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_2 = await orchestrator_2.run(
        incident=incident,
        source_capabilities=catalog,
    )

    assert result_1 == result_2


# ---------------------------------------------------------------------------
# Scenario 3: Resource Exhaustion Replay Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_resource_exhaustion_scenario() -> None:
    """Verify deterministic replay for resource exhaustion scenario."""
    incident = load_incident_seed("incident_resource_exhaustion.json")
    context = load_context_snapshot("context_resource_exhaustion.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_resource_exhaustion.json")

    provider_1 = RecordedReasoningProvider.from_file(fixture_path)
    provider_2 = RecordedReasoningProvider.from_file(fixture_path)

    collection_1 = InMemoryCollectionService()
    context_builder_1 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_1 = InvestigationOrchestrator(
        provider=provider_1,
        collection_service=collection_1,
        context_builder=context_builder_1,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_1 = await orchestrator_1.run(
        incident=incident,
        source_capabilities=catalog,
    )

    assert result_1.status == InvestigationStatus.COMPLETED
    assert len(result_1.hypotheses) >= 1
    assert result_1.hypotheses[0].hypothesis_id == "hyp_oom_01"
    assert "memory leak" in result_1.hypotheses[0].statement.lower()

    # Replay check
    collection_2 = InMemoryCollectionService()
    context_builder_2 = InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at)

    orchestrator_2 = InvestigationOrchestrator(
        provider=provider_2,
        collection_service=collection_2,
        context_builder=context_builder_2,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    result_2 = await orchestrator_2.run(
        incident=incident,
        source_capabilities=catalog,
    )

    assert result_1 == result_2


# ---------------------------------------------------------------------------
# Unavailable Sources & Uncertainty Generation Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unavailable_sources_produce_uncertainty_rather_than_invented_facts() -> None:
    """When operational data sources are unavailable, orchestrator reports uncertainty."""
    incident = load_incident_seed("incident_deployment_regression.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_deployment_regression.json")

    provider = RecordedReasoningProvider.from_file(fixture_path)
    collection = InMemoryCollectionService(default_status=SourceStatus.UNAVAILABLE)
    context_builder = InMemoryContextBuilder(synthetic_evidence=[])

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection,
        context_builder=context_builder,
    )

    result = await orchestrator.run(
        incident=incident,
        source_capabilities=catalog,
    )

    assert result.status == InvestigationStatus.INCONCLUSIVE
    assert result.stop_reason == StopReason.SOURCES_UNAVAILABLE
    assert len(result.remaining_uncertainty) >= 1
    assert any("unavailable" in u.lower() for u in result.remaining_uncertainty)
    assert result.hypotheses == []


# ---------------------------------------------------------------------------
# Provider Replacement Contract Neutrality Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_replacement_passes_same_contract_tests() -> None:
    """
    Replacing RecordedReasoningProvider with FakeReasoningProvider passes the exact same contract tests,
    confirming provider abstraction neutrality.
    """
    incident = load_incident_seed("incident_deployment_regression.json")
    context = load_context_snapshot("context_deployment_regression.json")
    catalog = load_catalog("catalog_default.json")
    fixture_path = get_fixture_path("recorded_deployment_regression.json")

    # Run with RecordedReasoningProvider
    rec_provider = RecordedReasoningProvider.from_file(fixture_path)
    rec_orchestrator = InvestigationOrchestrator(
        provider=rec_provider,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    rec_result = await rec_orchestrator.run(
        incident=incident,
        source_capabilities=catalog,
    )

    # Run with FakeReasoningProvider
    fake_provider = FakeReasoningProvider(preset="deployment-regression")
    fake_orchestrator = InvestigationOrchestrator(
        provider=fake_provider,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(synthetic_evidence=context.evidence, created_at=context.created_at),
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
    )

    fake_result = await fake_orchestrator.run(
        incident=incident,
        source_capabilities=catalog,
    )

    # Both must return valid RankedHypothesisSet instances with completed status
    assert isinstance(rec_result, RankedHypothesisSet)
    assert isinstance(fake_result, RankedHypothesisSet)

    assert rec_result.status == InvestigationStatus.COMPLETED
    assert fake_result.status == InvestigationStatus.COMPLETED

    assert len(rec_result.hypotheses) >= 1
    assert len(fake_result.hypotheses) >= 1

    assert rec_orchestrator.state_machine.current_state == InvestigationState.COMPLETED
    assert fake_orchestrator.state_machine.current_state == InvestigationState.COMPLETED
