"""
Unit and integration tests for LLMReasoningProvider.

Verifies Phase 9 requirements:
- Implements ReasoningProvider protocol
- Valid structured output for each method
- Invalid structured output triggers schema-repair attempt
- Repeated invalid output transitions to inconclusive / raises StructuredError
- Token and cost tracking are accurate
- Transient errors retry with bounded exponential backoff
- Credentials are not logged or stored in evidence / call records
- Provider-specific objects do not leak outside the adapter
- Full orchestration loop with LLMReasoningProvider produces valid RankedHypothesisSet
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
import pytest

from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog
from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    HypothesisStatus,
    InformationPriority,
    InformationValueLevel,
    InvestigationState,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
    Severity,
    SourceCoverageStatus,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import (
    REASONING_PROVIDER_ERROR,
    SCHEMA_VALIDATION_FAILED,
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
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    InvestigationBudget,
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.orchestration.orchestrator import (
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
)
from reasoning.provider.interface import ReasoningProvider
from reasoning.provider.llm_provider import (
    LLMCallRecord,
    LLMClient,
    LLMProviderError,
    LLMReasoningProvider,
    LLMResponse,
    ModelPricing,
)


# ---------------------------------------------------------------------------
# Mock LLM Client Implementation
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Mock LLM client returning configurable responses or exceptions."""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str | None = None,
        input_tokens: int = 150,
        output_tokens: int = 80,
        transient_failures_count: int = 0,
    ) -> None:
        self.responses = responses or {}
        self.default_response = default_response
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.transient_failures_remaining = transient_failures_count
        self.call_history: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        system_instruction: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.call_history.append({
            "prompt": prompt,
            "system_instruction": system_instruction,
            "json_schema": json_schema,
            "temperature": temperature,
        })

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise RuntimeError("Temporary 503 Service Unavailable")

        # Determine response
        content = self.default_response
        for key, resp in self.responses.items():
            if key in prompt:
                content = resp
                break

        if content is None:
            content = "{}"

        return LLMResponse(
            content=content,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_llm_01",
        external_alert_id="alt_ext_100",
        service="order-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 12, 1, 0, tzinfo=timezone.utc),
        summary="Spike in 500 error responses from order checkout",
    )


@pytest.fixture
def sample_catalog(sample_incident: IncidentSeed) -> SourceCapabilityCatalog:
    return SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=datetime(2026, 9, 12, 12, 1, 0, tzinfo=timezone.utc),
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
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    evidence_proj = EvidenceSummaryProjection(
        evidence_id="ev_101",
        source_type=SourceType.LOGS,
        evidence_type=EvidenceType.ERROR_EVENT,
        event_time=datetime(2026, 9, 12, 12, 0, 10, tzinfo=timezone.utc),
        summary="Database connection pool timeout observed",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_llm_01",
        revision=1,
        created_at=datetime(2026, 9, 12, 12, 2, 0, tzinfo=timezone.utc),
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
        maximum_hypotheses=4,
    )


# ---------------------------------------------------------------------------
# Phase 9 Tests
# ---------------------------------------------------------------------------


def test_llm_provider_implements_reasoning_provider_protocol() -> None:
    client = MockLLMClient()
    provider = LLMReasoningProvider(client=client)
    assert isinstance(provider, ReasoningProvider)


@pytest.mark.asyncio
async def test_llm_provider_valid_structured_output_all_methods(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # Pre-defined valid responses matching schemas
    mia_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "assessment_id": "mia_llm_001",
        "known_facts": [
            {"statement": "DB pool timeouts occurred", "evidence_ids": ["ev_101"]}
        ],
        "missing_information": [
            {
                "information_id": "need_deploy_01",
                "question": "Was a deployment performed recently?",
                "reason": "Identify if deployment caused config change",
                "priority": "high",
                "candidate_sources": ["deployments"],
                "resolved": False,
            }
        ],
        "unavailable_information": [],
        "recommended_stop": False,
    })

    plan_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "plan_id": "plan_llm_001",
        "round": 1,
        "queries": [
            {
                "query_id": "qry_llm_01",
                "source_type": "deployments",
                "question": "Recent deployments for order-service",
                "parameters": {"service": "order-service", "limit": 10},
                "related_information_ids": ["need_deploy_01"],
                "expected_information_value": "high",
            }
        ],
        "stop_reason": None,
    })

    hyp_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "revision": 1,
        "generated_at": "2026-09-12T12:05:00Z",
        "hypotheses": [
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_llm_01",
                "incident_id": sample_incident.incident_id,
                "revision": 1,
                "statement": "New deployment altered DB connection parameters.",
                "root_cause_category": "configuration_regression",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_101", "reason": "Connection timeout"}],
                "contradicting_evidence": [],
                "missing_information_ids": ["need_deploy_01"],
                "testable_prediction": "Deploy log indicates DB endpoint modification.",
                "status": "active",
            },
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_llm_02",
                "incident_id": sample_incident.incident_id,
                "revision": 1,
                "statement": "Database server underwent spontaneous connection saturation.",
                "root_cause_category": "resource_exhaustion",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_101", "reason": "Connection pool reached cap"}],
                "contradicting_evidence": [],
                "missing_information_ids": [],
                "testable_prediction": "DB metrics show peak connections.",
                "status": "active",
            }
        ]
    })

    revised_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "revision": 2,
        "generated_at": "2026-09-12T12:06:00Z",
        "hypotheses": [
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_llm_01",
                "incident_id": sample_incident.incident_id,
                "revision": 2,
                "statement": "New deployment altered DB connection parameters.",
                "root_cause_category": "configuration_regression",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_101", "reason": "Connection timeout"}],
                "contradicting_evidence": [],
                "missing_information_ids": [],
                "testable_prediction": "Deploy log indicates DB endpoint modification.",
                "status": "active",
            },
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_llm_02",
                "incident_id": sample_incident.incident_id,
                "revision": 2,
                "statement": "Database server underwent spontaneous connection saturation.",
                "root_cause_category": "resource_exhaustion",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_101", "reason": "Connection pool reached cap"}],
                "contradicting_evidence": [],
                "missing_information_ids": [],
                "testable_prediction": "DB metrics show peak connections.",
                "status": "rejected",
            }
        ]
    })

    responses = {
        "assess_missing_info:v1.0": mia_json,
        "plan_queries:v1.0": plan_json,
        "generate_hypotheses:v1.0": hyp_json,
        "revise_hypotheses:v1.0": revised_json,
    }

    client = MockLLMClient(responses=responses, input_tokens=200, output_tokens=100)
    pricing = ModelPricing(input_cost_per_million=1.0, output_cost_per_million=2.0)
    provider = LLMReasoningProvider(client=client, pricing=pricing, provider_name="openai", model="gpt-4o")

    # 1. Assess
    mia = await provider.assess_missing_information(sample_incident, sample_catalog, sample_context, [])
    assert isinstance(mia, MissingInformationAssessment)
    assert mia.assessment_id == "mia_llm_001"
    assert len(mia.missing_information) == 1

    # 2. Plan
    plan = await provider.plan_queries(mia, sample_catalog, sample_context, sample_budget)
    assert isinstance(plan, EvidenceQueryPlan)
    assert plan.plan_id == "plan_llm_001"
    assert len(plan.queries) == 1

    # 3. Generate
    hyp = await provider.generate_hypotheses(sample_incident, sample_context, sample_budget)
    assert isinstance(hyp, HypothesisSet)
    assert len(hyp.hypotheses) == 2
    assert hyp.hypotheses[0].hypothesis_id == "hyp_llm_01"

    # 4. Revise
    rev = await provider.revise_hypotheses(hyp, sample_context)
    assert isinstance(rev, HypothesisSet)
    assert rev.hypotheses[0].revision == 2
    assert rev.hypotheses[1].revision == 2
    assert rev.hypotheses[1].status == HypothesisStatus.REJECTED

    # Assert Call Records and Token Accounting
    assert len(provider.call_records) == 4
    for record in provider.call_records:
        assert isinstance(record, LLMCallRecord)
        assert record.provider == "openai"
        assert record.model == "gpt-4o"
        assert record.schema_version == "1.0"
        assert record.input_tokens == 200
        assert record.output_tokens == 100
        assert record.latency_seconds >= 0.0
        assert record.schema_repaired is False

    assert provider.total_input_tokens == 800
    assert provider.total_output_tokens == 400
    # Cost: 800 * 1.0 / 1M + 400 * 2.0 / 1M = 0.0008 + 0.0008 = 0.0016
    assert abs(provider.total_cost_usd - 0.0016) < 1e-6


@pytest.mark.asyncio
async def test_llm_provider_schema_repair_triggers_and_succeeds(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    # First response is malformed JSON (missing closing brace and quotes)
    malformed_json = '{"schema_version": "1.0", "incident_id": "inc_llm_01", "assessment_id": "mia_repaired"'

    # Second response (when repair is requested) is valid
    repaired_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "assessment_id": "mia_repaired",
        "known_facts": [],
        "missing_information": [],
        "unavailable_information": [],
        "recommended_stop": True,
    })

    client = MockLLMClient(
        responses={
            "assess_missing_info:v1.0": malformed_json,
            "failed schema validation": repaired_json,
        },
        input_tokens=100,
        output_tokens=50,
    )
    provider = LLMReasoningProvider(client=client)

    assessment = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )

    assert isinstance(assessment, MissingInformationAssessment)
    assert assessment.assessment_id == "mia_repaired"
    assert assessment.recommended_stop is True

    record = provider.get_last_call_record()
    assert record is not None
    assert record.schema_repaired is True
    assert record.input_tokens == 200  # Combined tokens from initial + repair
    assert record.output_tokens == 100


@pytest.mark.asyncio
async def test_llm_provider_schema_repair_failure_raises_structured_error(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    # Malformed response for both initial and repair calls
    client = MockLLMClient(default_response="Totally not valid json {{{")
    provider = LLMReasoningProvider(client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.assess_missing_information(
            incident=sample_incident,
            source_capabilities=sample_catalog,
            context=sample_context,
            active_hypotheses=[],
        )

    err = exc_info.value.error
    assert err.code == SCHEMA_VALIDATION_FAILED
    assert "schema validation after repair attempt" in err.message


@pytest.mark.asyncio
async def test_llm_provider_transient_retry_and_backoff(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    valid_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "assessment_id": "mia_retry_ok",
        "known_facts": [],
        "missing_information": [],
        "unavailable_information": [],
        "recommended_stop": False,
    })

    # 1 transient failure before success
    client = MockLLMClient(default_response=valid_json, transient_failures_count=1)
    provider = LLMReasoningProvider(
        client=client,
        max_retries=2,
        initial_backoff_seconds=0.01,
        backoff_multiplier=1.5,
    )

    mia = await provider.assess_missing_information(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        context=sample_context,
        active_hypotheses=[],
    )

    assert mia.assessment_id == "mia_retry_ok"
    record = provider.get_last_call_record()
    assert record is not None
    assert record.retry_count == 1


@pytest.mark.asyncio
async def test_llm_provider_credentials_not_logged_or_stored(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    secret_key = "sk-super-secret-api-token-12345"
    valid_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "assessment_id": "mia_privacy",
        "known_facts": [],
        "missing_information": [],
        "unavailable_information": [],
        "recommended_stop": False,
    })

    client = MockLLMClient(default_response=valid_json)
    provider = LLMReasoningProvider(client=client, api_key=secret_key)

    mia = await provider.assess_missing_information(
        sample_incident, sample_catalog, sample_context, []
    )

    # Verify secret is not in serialized assessment
    assert secret_key not in mia.model_dump_json()

    # Verify secret is not in any call record
    for record in provider.call_records:
        record_str = str(record)
        assert secret_key not in record_str


@pytest.mark.asyncio
async def test_llm_provider_end_to_end_orchestration_loop(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
) -> None:
    """Run full investigation loop using LLMReasoningProvider adapter."""
    mia_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "assessment_id": "mia_e2e_01",
        "known_facts": [],
        "missing_information": [
            {
                "information_id": "need_logs_01",
                "question": "Check recent error logs",
                "reason": "Identify error signatures",
                "priority": "high",
                "candidate_sources": ["logs"],
                "resolved": False,
            }
        ],
        "unavailable_information": [],
        "recommended_stop": False,
    })

    plan_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "plan_id": "plan_e2e_01",
        "round": 1,
        "queries": [
            {
                "query_id": "qry_e2e_01",
                "source_type": "logs",
                "question": "Fetch recent error logs",
                "parameters": {"service": "order-service", "limit": 10},
                "related_information_ids": ["need_logs_01"],
                "expected_information_value": "high",
            }
        ],
        "stop_reason": None,
    })

    hyp_json = json.dumps({
        "schema_version": "1.0",
        "incident_id": sample_incident.incident_id,
        "revision": 1,
        "generated_at": "2026-09-12T12:10:00Z",
        "hypotheses": [
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_e2e_01",
                "incident_id": sample_incident.incident_id,
                "revision": 1,
                "statement": "Application crashed due to unhandled configuration error.",
                "root_cause_category": "configuration_regression",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_logs_rec_qry_e2e_01_1", "reason": "Error observed in logs"}],
                "contradicting_evidence": [],
                "missing_information_ids": [],
                "testable_prediction": "Crash backtrace points to config loading.",
                "status": "active",
            },
            {
                "schema_version": "1.0",
                "hypothesis_id": "hyp_e2e_02",
                "incident_id": sample_incident.incident_id,
                "revision": 1,
                "statement": "Network latency between service and cache.",
                "root_cause_category": "resource_exhaustion",
                "affected_component": "order-service",
                "supporting_evidence": [{"evidence_id": "ev_logs_rec_qry_e2e_01_1", "reason": "Network timeout in log"}],
                "contradicting_evidence": [],
                "missing_information_ids": [],
                "testable_prediction": "Ping packets dropped.",
                "status": "active",
            }
        ]
    })

    client = MockLLMClient(
        responses={
            "assess_missing_info:v1.0": mia_json,
            "plan_queries:v1.0": plan_json,
            "generate_hypotheses:v1.0": hyp_json,
        }
    )
    provider = LLMReasoningProvider(client=client)

    collection_svc = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder()
    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_svc,
        context_builder=context_builder,
    )

    ranked_set = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
        budget=InvestigationBudget(max_rounds=1),
    )

    assert isinstance(ranked_set, RankedHypothesisSet)
    assert ranked_set.status == InvestigationStatus.COMPLETED
    assert len(ranked_set.hypotheses) == 2
    assert ranked_set.hypotheses[0].rank == 1
    assert ranked_set.hypotheses[1].rank == 2
    assert ranked_set.hypotheses[0].evidence_score > 0.0


@pytest.mark.asyncio
async def test_llm_provider_failure_transitions_orchestrator_to_inconclusive(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
) -> None:
    """Repeated invalid output from LLM triggers schema repair failure and transitions orchestrator to inconclusive."""
    # Always return broken JSON
    client = MockLLMClient(default_response="INVALID_JSON_NOT_RECOVERABLE")
    provider = LLMReasoningProvider(client=client)

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=InMemoryCollectionService(),
        context_builder=InMemoryContextBuilder(),
    )

    ranked_set = await orchestrator.run(
        incident=sample_incident,
        source_capabilities=sample_catalog,
    )

    assert isinstance(ranked_set, RankedHypothesisSet)
    assert ranked_set.status == InvestigationStatus.INCONCLUSIVE
    assert ranked_set.stop_reason == StopReason.INSUFFICIENT_EVIDENCE
    assert len(ranked_set.hypotheses) == 0
