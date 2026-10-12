"""Unit tests for the plan_remediation LangGraph node."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from pydantic import BaseModel, Field

from contracts.enums import Severity
from contracts.errors.schemas import ProgressEvent
from contracts.evidence.schemas import IncidentContextSnapshot, IncidentSummary
from contracts.hypothesis.schemas import RankedHypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import BudgetUsage
from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes.remediate import plan_remediation
from investigation.graph.ports import RemediationPlanningService


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class MockRemediationStep(BaseModel):
    step_number: int = 1
    title: str = "Roll back configuration"
    purpose: str = "Revert database pool size setting"
    instructions: list[str] = ["Connect to management cluster", "Revert max_pool_size to 50"]
    expected_result: str = "Database connection pool latency normalizes"
    verification: list[str] = ["Check p99 latency metric", "Verify error rate < 0.1%"]
    rollback_guidance: list[str] = ["Restore previous config map if connection errors spike"]
    requires_human_approval: bool = True


class MockRemediationPlan(BaseModel):
    plan_id: str = "plan-test-1"
    incident_id: str = "inc-test-1"
    created_at: datetime = Field(default_factory=lambda: NOW)
    recommendation_available: bool = True
    safety_notice: str = "Advisory guidance only. Human operator approval is required."
    hypothesis_id: str | None = "hyp-1"
    root_cause_category: str | None = "configuration"
    confidence: str | None = "high"
    evidence_ids: list[str] = Field(default_factory=lambda: ["ev-1", "ev-2"])
    risk: str = "low"
    prerequisites: list[str] = Field(default_factory=lambda: ["Verify cluster access"])
    steps: list[MockRemediationStep] = Field(default_factory=lambda: [MockRemediationStep()])
    escalation_guidance: list[str] = Field(default_factory=list)
    unresolved_uncertainty: list[str] = Field(default_factory=list)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingProgressSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


class ScriptedRemediationService:
    def __init__(self, plan_to_return: MockRemediationPlan) -> None:
        self.plan_to_return = plan_to_return
        self.received_ranked = None
        self.received_context = None

    async def plan(self, ranked: RankedHypothesisSet, context: IncidentContextSnapshot):
        self.received_ranked = ranked
        self.received_context = context
        return self.plan_to_return


def sample_state() -> dict:
    incident = IncidentSeed(
        incident_id="inc-test-1",
        external_alert_id="alert-1",
        service="order-processor",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=NOW,
        received_at=NOW,
        summary="Elevated database checkout timeouts.",
    )
    summary = IncidentSummary(
        service=incident.service,
        environment=incident.environment,
        severity=incident.severity,
        detected_at=incident.detected_at,
        summary=incident.summary,
    )
    context = IncidentContextSnapshot(
        snapshot_id="snap-1",
        incident_id=incident.incident_id,
        revision=1,
        created_at=NOW,
        incident=summary,
        source_coverage={},
        timeline=[],
        evidence=[],
    )
    ranked = RankedHypothesisSet(
        incident_id="inc-test-1",
        context_snapshot_id="snap-1",
        ranking_id="rank-1",
        created_at=NOW,
        status="completed",
        hypotheses=[],
        remaining_uncertainty=[],
        budget_usage=BudgetUsage(rounds=1, queries=1, reasoning_calls=1),
    )
    return {
        "incident": incident,
        "context": context,
        "ranked_result": ranked,
        "progress_events": [],
    }


def make_dependencies(service: RemediationPlanningService | None) -> tuple[GraphDependencies, RecordingProgressSink]:
    sink = RecordingProgressSink()
    deps = GraphDependencies(
        collection_service=None,  # type: ignore
        context_builder=None,  # type: ignore
        missing_information_service=None,  # type: ignore
        query_planning_service=None,  # type: ignore
        hypothesis_service=None,  # type: ignore
        stopping_service=None,  # type: ignore
        ranking_service=None,  # type: ignore
        progress_sink=sink,
        clock=FixedClock(),
        remediation_planning_service=service,
    )
    return deps, sink


@pytest.mark.asyncio
async def test_remediation_service_conforms_to_protocol() -> None:
    service = ScriptedRemediationService(MockRemediationPlan())
    assert isinstance(service, RemediationPlanningService)


@pytest.mark.asyncio
async def test_plan_remediation_invokes_service_and_emits_event() -> None:
    expected_plan = MockRemediationPlan()
    service = ScriptedRemediationService(expected_plan)
    deps, sink = make_dependencies(service)
    state = sample_state()

    result = await plan_remediation(state, deps)

    assert service.received_ranked is state["ranked_result"]
    assert service.received_context is state["context"]
    assert result["remediation_plan"] is expected_plan

    events = result["progress_events"]
    assert len(events) == 1
    event = events[0]
    assert event.stage == "plan_remediation"
    assert event.kind == "status"
    assert event.title == "Remediation guidance prepared"
    assert event.detail == expected_plan.safety_notice

    assert len(sink.events) == 1
    assert sink.events[0].stage == "plan_remediation"


@pytest.mark.asyncio
async def test_plan_remediation_handles_blocked_plan() -> None:
    blocked_plan = MockRemediationPlan(
        recommendation_available=False,
        risk="blocked",
        steps=[],
        escalation_guidance=["Escalate to database on-call engineer."],
        unresolved_uncertainty=["Database replica lag causes unknown."],
        safety_notice="No automated or recommended change. Human escalation required.",
    )
    service = ScriptedRemediationService(blocked_plan)
    deps, sink = make_dependencies(service)
    state = sample_state()

    result = await plan_remediation(state, deps)

    assert result["remediation_plan"].recommendation_available is False
    assert result["remediation_plan"].risk == "blocked"
    assert len(result["remediation_plan"].steps) == 0
    assert result["progress_events"][0].detail == blocked_plan.safety_notice


@pytest.mark.asyncio
async def test_plan_remediation_progress_events_scrub_raw_credentials() -> None:
    plan = MockRemediationPlan(
        safety_notice="Advisory guidance only. Verify system health.",
    )
    service = ScriptedRemediationService(plan)
    deps, sink = make_dependencies(service)
    state = sample_state()

    result = await plan_remediation(state, deps)
    for event in result["progress_events"]:
        assert "password" not in event.detail.lower()
        assert "bearer" not in event.detail.lower()
        assert "secret" not in event.detail.lower()


@pytest.mark.asyncio
async def test_plan_remediation_handles_none_service_gracefully() -> None:
    deps, sink = make_dependencies(None)
    state = sample_state()

    result = await plan_remediation(state, deps)

    assert result["remediation_plan"] is None
    assert len(result["progress_events"]) == 1
    assert "skipped" in result["progress_events"][0].title
