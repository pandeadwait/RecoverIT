"""
Unit tests for ReasoningProvider abstraction, FakeReasoningProvider, and RecordedReasoningProvider.

Verifies:
- Both providers satisfy the ReasoningProvider runtime protocol.
- Fake provider returns valid schema-conforming outputs for all presets and methods.
- Recorded provider replays recorded responses deterministically from fixtures and memory.
- Swapping providers does not change the caller contract.
- Provider-specific response objects do not leak outside the adapter.

See WORK_DIVISION.md §8.7 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest

from contracts.collection.schemas import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.common import (
    EvidenceType,
    HypothesisStatus,
    Reliability,
    Severity,
    SourceCoverageStatus,
    SourceType,
)
from contracts.evidence.schemas import (
    EvidenceProvenance,
    EvidenceQuality,
    EvidenceRecord,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    Hypothesis,
    HypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    InvestigationBudget,
    MissingInformationAssessment,
)
from reasoning.provider import (
    FakeReasoningProvider,
    ReasoningProvider,
    RecordedReasoningProvider,
    UnrecordedRequestError,
)


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_test_01",
        external_alert_id="alt_ext_100",
        service="payment-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="HTTP 500 error spike detected",
        labels={"tier": "1", "team": "payments"},
    )


@pytest.fixture
def sample_catalog(sample_incident: IncidentSeed) -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        sources=[
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit"],
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
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    evidence_proj = EvidenceSummaryProjection(
        evidence_id="ev_100",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        summary="High error rate on /checkout",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )

    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_test_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 2, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        timeline=[],
        evidence=[evidence_proj],
        source_coverage={SourceType.LOGS: SourceCoverageStatus.AVAILABLE},
    )


@pytest.fixture
def sample_budget() -> InvestigationBudget:
    return InvestigationBudget(
        max_rounds=3,
        max_queries=6,
        max_elapsed_seconds=300,
        max_reasoning_calls=5,
        max_input_units=10_000,
        max_output_units=2_000,
        minimum_hypotheses=2,
        maximum_hypotheses=5,
    )


# ---------------------------------------------------------------------------
# Protocol Compliance Tests
# ---------------------------------------------------------------------------


def test_providers_implement_protocol() -> None:
    fake = FakeReasoningProvider()
    recorded = RecordedReasoningProvider()

    assert isinstance(fake, ReasoningProvider)
    assert isinstance(recorded, ReasoningProvider)


# ---------------------------------------------------------------------------
# FakeReasoningProvider Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset",
    [
        "deployment-regression",
        "database-outage",
        "resource-exhaustion",
        "dependency-incompatibility",
        "coincidental-deployment",
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_all_presets_and_methods(
    preset: str,
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    provider = FakeReasoningProvider(preset=preset)

    # 1. assess_missing_information
    assessment = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert isinstance(assessment, MissingInformationAssessment)
    assert assessment.incident_id == sample_incident.incident_id
    assert len(assessment.missing_information) > 0
    # Verify round-trip JSON serialization
    assert MissingInformationAssessment.model_validate_json(assessment.model_dump_json()) == assessment

    # 2. plan_queries
    plan = await provider.plan_queries(
        missing_information=assessment,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )
    assert isinstance(plan, EvidenceQueryPlan)
    assert plan.incident_id == sample_incident.incident_id
    assert len(plan.queries) > 0
    assert EvidenceQueryPlan.model_validate_json(plan.model_dump_json()) == plan

    # 3. generate_hypotheses
    hypotheses = await provider.generate_hypotheses(
        incident=sample_incident,
        context=sample_context,
        limits=sample_budget,
    )
    assert isinstance(hypotheses, HypothesisSet)
    assert hypotheses.incident_id == sample_incident.incident_id
    assert len(hypotheses.hypotheses) >= sample_budget.minimum_hypotheses
    assert HypothesisSet.model_validate_json(hypotheses.model_dump_json()) == hypotheses

    # 4. revise_hypotheses
    revised = await provider.revise_hypotheses(
        previous_hypotheses=hypotheses,
        new_context=sample_context,
    )
    assert isinstance(revised, HypothesisSet)
    assert revised.incident_id == sample_incident.incident_id
    assert len(revised.hypotheses) == len(hypotheses.hypotheses)
    assert HypothesisSet.model_validate_json(revised.model_dump_json()) == revised

    # Verify call logging
    assert provider.call_count == 4
    provider.reset_calls()
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_fake_provider_custom_overrides(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    custom_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="custom_mia_999",
        known_facts=[],
        missing_information=[],
        unavailable_information=[],
        recommended_stop=True,
    )

    provider = FakeReasoningProvider(custom_assessment=custom_assessment)
    res = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert res.assessment_id == "custom_mia_999"
    assert res.recommended_stop is True


# ---------------------------------------------------------------------------
# RecordedReasoningProvider Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recorded_provider_from_fixture_file(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    fixture_path = Path("tests/fixtures/person3/recorded_session_01.json")
    provider = RecordedReasoningProvider(fixture_path=fixture_path)

    # 1. assess_missing_information
    assessment = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert isinstance(assessment, MissingInformationAssessment)
    assert assessment.assessment_id == "mia_rec_001"
    assert assessment.missing_information[0].information_id == "need_deploy"

    # 2. plan_queries
    plan = await provider.plan_queries(
        missing_information=assessment,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )
    assert isinstance(plan, EvidenceQueryPlan)
    assert plan.plan_id == "plan_rec_001"
    assert plan.queries[0].query_id == "qry_rec_001"

    # 3. generate_hypotheses
    hypotheses = await provider.generate_hypotheses(
        incident=sample_incident,
        context=sample_context,
        limits=sample_budget,
    )
    assert isinstance(hypotheses, HypothesisSet)
    assert len(hypotheses.hypotheses) == 2
    assert hypotheses.hypotheses[0].hypothesis_id == "hyp_rec_01"
    assert hypotheses.hypotheses[0].status == HypothesisStatus.ACTIVE

    # 4. revise_hypotheses
    revised = await provider.revise_hypotheses(
        previous_hypotheses=hypotheses,
        new_context=sample_context,
    )
    assert isinstance(revised, HypothesisSet)
    assert len(revised.hypotheses) == 2
    assert revised.hypotheses[0].revision == 2
    assert revised.hypotheses[1].status == HypothesisStatus.REJECTED

    assert provider.call_count == 4


@pytest.mark.asyncio
async def test_recorded_provider_composite_key_matching(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    recordings = {
        f"{sample_incident.incident_id}:assess_missing_information:1": {
            "schema_version": "1.0",
            "incident_id": sample_incident.incident_id,
            "assessment_id": "composite_mia_001",
            "known_facts": [],
            "missing_information": [],
            "unavailable_information": [],
            "recommended_stop": False,
        }
    }

    provider = RecordedReasoningProvider(recordings=recordings)
    assessment = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert assessment.assessment_id == "composite_mia_001"


@pytest.mark.asyncio
async def test_recorded_provider_sequential_queue(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    recordings = {
        "assess_missing_information": [
            {
                "schema_version": "1.0",
                "incident_id": sample_incident.incident_id,
                "assessment_id": "queue_mia_01",
                "known_facts": [],
                "missing_information": [],
                "unavailable_information": [],
                "recommended_stop": False,
            },
            {
                "schema_version": "1.0",
                "incident_id": sample_incident.incident_id,
                "assessment_id": "queue_mia_02",
                "known_facts": [],
                "missing_information": [],
                "unavailable_information": [],
                "recommended_stop": True,
            },
        ]
    }

    provider = RecordedReasoningProvider(recordings=recordings)

    first = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert first.assessment_id == "queue_mia_01"

    second = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )
    assert second.assessment_id == "queue_mia_02"
    assert second.recommended_stop is True


@pytest.mark.asyncio
async def test_recorded_provider_unrecorded_request_raises(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    provider = RecordedReasoningProvider(recordings={})

    with pytest.raises(UnrecordedRequestError) as exc_info:
        await provider.assess_missing_information(
            incident=sample_incident,
            source_capabilities=sample_catalog,
            context=sample_context,
            active_hypotheses=[],
        )

    assert "No recorded response found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Provider Neutrality & Swapping Test
# ---------------------------------------------------------------------------


async def execute_reasoning_step(
    provider: ReasoningProvider,
    incident: IncidentSeed,
    catalog: SourceCapabilityCatalog,
    context: IncidentContextSnapshot,
) -> MissingInformationAssessment:
    """Demonstrates caller neutrality: provider is swapped without any contract change."""
    return await provider.assess_missing_information(
        incident=incident,
        source_capabilities=catalog,
        context=context,
        active_hypotheses=[],
    )


@pytest.mark.asyncio
async def test_provider_swapping_neutrality(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    fake = FakeReasoningProvider()
    recorded = RecordedReasoningProvider(
        fixture_path=Path("tests/fixtures/person3/recorded_session_01.json")
    )

    out_fake = await execute_reasoning_step(fake, sample_incident, sample_catalog, sample_context)
    out_rec = await execute_reasoning_step(recorded, sample_incident, sample_catalog, sample_context)

    # Both return the exact domain contract model
    assert isinstance(out_fake, MissingInformationAssessment)
    assert isinstance(out_rec, MissingInformationAssessment)

    # No provider-specific types leaked
    assert type(out_fake) is MissingInformationAssessment
    assert type(out_rec) is MissingInformationAssessment
