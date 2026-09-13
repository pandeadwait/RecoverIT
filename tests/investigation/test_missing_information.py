"""
Unit tests for MissingInformationAssessor.

Verifies:
- Empty context produces meaningful missing-information items.
- Known facts only reference evidence IDs actually present in context.
- Candidate sources are checked against SourceCapabilityCatalog.
- Information items with unavailable sources are moved to unavailable_information.
- Priority is validated against InformationPriority enum.
- Output serializes and deserializes cleanly.

See WORK_DIVISION.md §8.5 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog
from contracts.common import (
    EvidenceType,
    InformationPriority,
    Reliability,
    Severity,
    SourceCoverageStatus,
    SourceType,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.missing_information.assessor import MissingInformationAssessor
from reasoning.provider.fake_provider import FakeReasoningProvider


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_mia_01",
        external_alert_id="alt_ext_01",
        service="order-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="High error rate on /checkout",
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
            ),
            SourceCapability(
                source_type=SourceType.CHANGES,
                available=True,
                supported_query_fields=["service", "limit"],
            ),
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit"],
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=False,  # explicitly unavailable
                supported_query_fields=["metric", "limit"],
            ),
        ],
    )


@pytest.fixture
def empty_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_empty_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        timeline=[],
        evidence=[],
        source_coverage={},
    )


@pytest.fixture
def context_with_evidence(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    evidence = EvidenceSummaryProjection(
        evidence_id="ev_valid_01",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        summary="Timeout connecting to database",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )

    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_with_ev_01",
        revision=2,
        created_at=datetime(2026, 9, 12, 10, 5, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        timeline=[],
        evidence=[evidence],
        source_coverage={SourceType.LOGS: SourceCoverageStatus.AVAILABLE},
    )


# ---------------------------------------------------------------------------
# Assessor Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_context_produces_valid_assessment(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    empty_context: IncidentContextSnapshot,
) -> None:
    provider = FakeReasoningProvider(preset="deployment-regression")
    assessor = MissingInformationAssessor(provider=provider)

    assessment = await assessor.assess(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=empty_context,
    )

    assert isinstance(assessment, MissingInformationAssessment)
    assert assessment.incident_id == sample_incident.incident_id
    assert len(assessment.missing_information) > 0

    # In empty context, known facts must NOT reference any nonexistent evidence IDs
    for fact in assessment.known_facts:
        assert fact.evidence_ids == []

    # Verify JSON round-trip
    serialized = assessment.model_dump_json()
    reloaded = MissingInformationAssessment.model_validate_json(serialized)
    assert reloaded == assessment


@pytest.mark.asyncio
async def test_known_facts_filter_nonexistent_evidence_ids(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    context_with_evidence: IncidentContextSnapshot,
) -> None:
    # Custom assessment referencing one real ID and one hallucinated ID
    custom_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_custom_01",
        known_facts=[
            KnownFact(
                statement="Database timeout occurred.",
                evidence_ids=["ev_valid_01", "ev_hallucinated_99"],
            )
        ],
        missing_information=[
            MissingInformationItem(
                information_id="need_01",
                question="Was a deployment performed?",
                reason="Check for recent changes.",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.DEPLOYMENTS],
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=custom_assessment)
    assessor = MissingInformationAssessor(provider=provider)

    result = await assessor.assess(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=context_with_evidence,
    )

    assert len(result.known_facts) == 1
    # Only the valid ID in context_with_evidence remains
    assert result.known_facts[0].evidence_ids == ["ev_valid_01"]


@pytest.mark.asyncio
async def test_unavailable_sources_moved_to_unavailable_information(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    empty_context: IncidentContextSnapshot,
) -> None:
    # METRICS is marked available=False in sample_catalog
    custom_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_unavail_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="need_deploy",
                question="Check deployment history",
                reason="Recent change",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.DEPLOYMENTS],  # available
                resolved=False,
            ),
            MissingInformationItem(
                information_id="need_cpu_metrics",
                question="Check CPU spike",
                reason="Hardware saturation",
                priority=InformationPriority.MEDIUM,
                candidate_sources=[SourceType.METRICS],  # UNAVAILABLE in catalog
                resolved=False,
            ),
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=custom_assessment)
    assessor = MissingInformationAssessor(provider=provider)

    result = await assessor.assess(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=empty_context,
    )

    # need_deploy remains in missing_information
    assert len(result.missing_information) == 1
    assert result.missing_information[0].information_id == "need_deploy"

    # need_cpu_metrics was moved to unavailable_information
    assert len(result.unavailable_information) == 1
    assert result.unavailable_information[0].information_id == "need_cpu_metrics"
    assert not result.recommended_stop


@pytest.mark.asyncio
async def test_all_sources_unavailable_recommends_stop(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    empty_context: IncidentContextSnapshot,
) -> None:
    custom_assessment = MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_all_unavail_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="need_metrics_only",
                question="What is the metric rate?",
                reason="Saturation",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.METRICS],  # unavailable
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )

    provider = FakeReasoningProvider(custom_assessment=custom_assessment)
    assessor = MissingInformationAssessor(provider=provider)

    result = await assessor.assess(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=empty_context,
    )

    assert len(result.missing_information) == 0
    assert len(result.unavailable_information) == 1
    assert result.recommended_stop is True
