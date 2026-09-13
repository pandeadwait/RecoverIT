"""
Unit tests for EvidenceQueryPlanner.

Verifies:
- Queries only use capabilities advertised in SourceCapabilityCatalog.
- Rejects queries with unsupported parameter fields.
- Rejects queries exceeding maximum_items limit.
- Rejects queries exceeding maximum_window_seconds limit.
- Duplicate queries are detected and rejected.
- Budget enforcement truncates or rejects queries when budget is reached.
- A plan with zero queries always has a stop_reason.
- Structured warnings are recorded for filtered/rejected queries.

See WORK_DIVISION.md §8.5, §8.9 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog
from contracts.common import (
    InformationPriority,
    InformationValueLevel,
    Severity,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import (
    BUDGET_EXHAUSTED,
    DUPLICATE_QUERY,
    INVALID_QUERY,
    SOURCE_UNAVAILABLE,
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
    MissingInformationAssessment,
    MissingInformationItem,
)
from investigation.budgets.budget_tracker import BudgetTracker
from investigation.query_planning.planner import EvidenceQueryPlanner
from reasoning.provider.fake_provider import FakeReasoningProvider


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_plan_01",
        external_alert_id="alt_ext_01",
        service="payment-api",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="Payment failure rate elevated",
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
                maximum_window_seconds=86400,
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit", "start_time", "end_time"],
                maximum_window_seconds=3600,
                maximum_items=200,
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=False,  # unavailable
                supported_query_fields=["metric", "limit"],
                maximum_window_seconds=3600,
                maximum_items=100,
            ),
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        incident_id=sample_incident.incident_id,
        snapshot_id="ctx_plan_01",
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
        evidence=[],
        source_coverage={},
    )


@pytest.fixture
def sample_budget() -> InvestigationBudget:
    return InvestigationBudget(
        max_rounds=3,
        max_queries=5,
        max_elapsed_seconds=300,
        max_reasoning_calls=5,
        max_input_units=10_000,
        max_output_units=2_000,
    )


@pytest.fixture
def sample_missing_info(sample_incident: IncidentSeed) -> MissingInformationAssessment:
    return MissingInformationAssessment(
        incident_id=sample_incident.incident_id,
        assessment_id="mia_plan_01",
        known_facts=[],
        missing_information=[
            MissingInformationItem(
                information_id="need_01",
                question="Was a deployment made recently?",
                reason="Determine change consistency.",
                priority=InformationPriority.HIGH,
                candidate_sources=[SourceType.DEPLOYMENTS],
                resolved=False,
            )
        ],
        unavailable_information=[],
        recommended_stop=False,
    )


# ---------------------------------------------------------------------------
# Planner Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_plan_generation(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    provider = FakeReasoningProvider(preset="deployment-regression")
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
        round_num=1,
    )

    assert isinstance(plan, EvidenceQueryPlan)
    assert plan.incident_id == sample_missing_info.incident_id
    assert len(plan.queries) > 0
    assert plan.stop_reason is None
    assert len(planner.last_warnings) == 0

    # Test round-trip JSON serialization
    assert EvidenceQueryPlan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.asyncio
async def test_rejects_unavailable_source_type(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # Query METRICS which is marked available=False
    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_unavail_01",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_metrics_01",
                source_type=SourceType.METRICS,
                question="Check metrics",
                parameters={"metric": "cpu_utilization", "limit": 10},
                related_information_ids=["need_01"],
            )
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )

    # The query was rejected
    assert len(plan.queries) == 0
    assert plan.stop_reason is not None
    assert any(w.code == SOURCE_UNAVAILABLE for w in planner.last_warnings)


@pytest.mark.asyncio
async def test_rejects_unsupported_parameter_fields(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # DEPLOYMENTS only supports 'service' and 'limit', not 'arbitrary_sql'
    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_invalid_field_01",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_bad_field",
                source_type=SourceType.DEPLOYMENTS,
                question="Get deployments with custom filter",
                parameters={"service": "payment-api", "arbitrary_field": "injected"},
                related_information_ids=["need_01"],
            )
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )

    assert len(plan.queries) == 0
    assert any(w.code == INVALID_QUERY for w in planner.last_warnings)
    assert "arbitrary_field" in planner.last_warnings[0].message


@pytest.mark.asyncio
async def test_rejects_queries_exceeding_item_limit(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # DEPLOYMENTS maximum_items is 50, query requests limit=500
    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_limit_overflow",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_huge_limit",
                source_type=SourceType.DEPLOYMENTS,
                question="Fetch all deployments",
                parameters={"service": "payment-api", "limit": 500},
                related_information_ids=["need_01"],
            )
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )

    assert len(plan.queries) == 0
    assert any(w.code == INVALID_QUERY for w in planner.last_warnings)
    assert "exceeds maximum_items" in planner.last_warnings[0].message


@pytest.mark.asyncio
async def test_rejects_queries_exceeding_time_window(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # LOGS maximum_window_seconds is 3600 (1 hour). Query asks for 24 hours.
    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_time_overflow",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_wide_window",
                source_type=SourceType.LOGS,
                question="Fetch 24 hours of logs",
                parameters={
                    "service": "payment-api",
                    "start_time": "2026-09-11T10:00:00Z",
                    "end_time": "2026-09-12T10:00:00Z",
                    "limit": 50,
                },
                related_information_ids=["need_01"],
            )
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )

    assert len(plan.queries) == 0
    assert any(w.code == INVALID_QUERY for w in planner.last_warnings)
    assert "exceeds maximum_window_seconds" in planner.last_warnings[0].message


@pytest.mark.asyncio
async def test_rejects_duplicate_queries_in_plan(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # Two identical queries in the same plan
    q1 = EvidenceQueryPlanQuery(
        query_id="qry_01",
        source_type=SourceType.DEPLOYMENTS,
        question="Check deployments",
        parameters={"service": "payment-api", "limit": 10},
    )
    q2 = EvidenceQueryPlanQuery(
        query_id="qry_02",
        source_type=SourceType.DEPLOYMENTS,
        question="Check deployments duplicate",
        parameters={"service": "payment-api", "limit": 10},
    )

    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_duplicate",
        round=1,
        queries=[q1, q2],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
    )

    # First accepted, duplicate rejected
    assert len(plan.queries) == 1
    assert plan.queries[0].query_id == "qry_01"
    assert any(w.code == DUPLICATE_QUERY for w in planner.last_warnings)


@pytest.mark.asyncio
async def test_rejects_duplicate_queries_against_history(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    history_query = EvidenceQueryPlanQuery(
        query_id="qry_hist_01",
        source_type=SourceType.DEPLOYMENTS,
        question="Already executed deployment query",
        parameters={"service": "payment-api", "limit": 10},
    )

    candidate_query = EvidenceQueryPlanQuery(
        query_id="qry_cand_01",
        source_type=SourceType.DEPLOYMENTS,
        question="Duplicate of previous round",
        parameters={"service": "payment-api", "limit": 10},
    )

    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_hist_dup",
        round=2,
        queries=[candidate_query],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
        round_num=2,
        history_queries=[history_query],
    )

    assert len(plan.queries) == 0
    assert any(w.code == DUPLICATE_QUERY for w in planner.last_warnings)


@pytest.mark.asyncio
async def test_budget_enforcement_truncates_overplanning(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # Tracker has only 1 query remaining
    tracker = BudgetTracker(budget=sample_budget)
    tracker.record_queries(4)  # max_queries is 5, so 1 remaining

    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_budget_trunc",
        round=2,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_01",
                source_type=SourceType.DEPLOYMENTS,
                question="Deployments",
                parameters={"service": "payment-api", "limit": 5},
            ),
            EvidenceQueryPlanQuery(
                query_id="qry_02",
                source_type=SourceType.LOGS,
                question="Logs",
                parameters={"service": "payment-api", "limit": 10},
            ),
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider, budget_tracker=tracker)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
        round_num=2,
    )

    # Only 1 query fits in remaining budget
    assert len(plan.queries) == 1
    assert plan.queries[0].query_id == "qry_01"
    assert any(w.code == BUDGET_EXHAUSTED for w in planner.last_warnings)


@pytest.mark.asyncio
async def test_zero_query_plan_has_stop_reason(
    sample_missing_info: MissingInformationAssessment,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
    sample_budget: InvestigationBudget,
) -> None:
    # Tracker with 0 queries remaining
    tracker = BudgetTracker(budget=sample_budget)
    tracker.record_queries(5)  # 0 remaining

    raw_plan = EvidenceQueryPlan(
        incident_id=sample_missing_info.incident_id,
        plan_id="plan_zero",
        round=3,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="qry_01",
                source_type=SourceType.DEPLOYMENTS,
                question="Deployments",
                parameters={"service": "payment-api", "limit": 5},
            )
        ],
    )

    provider = FakeReasoningProvider(custom_plan=raw_plan)
    planner = EvidenceQueryPlanner(provider=provider, budget_tracker=tracker)

    plan = await planner.plan(
        missing_information=sample_missing_info,
        source_capabilities=sample_catalog,
        context=sample_context,
        budget=sample_budget,
        round_num=3,
    )

    assert len(plan.queries) == 0
    assert plan.stop_reason == StopReason.BUDGET_EXHAUSTED
