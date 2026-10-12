"""Integration tests for LangGraph routing to plan_remediation and checkpoint resume."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from contracts.collection.schemas import (
    EvidenceQuery,
    EvidenceQueryPlan,
    RawEvidenceBatch,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.enums import (
    InvestigationState,
    InvestigationStatus,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopAction,
    StopReason,
)
from contracts.evidence.schemas import IncidentContextSnapshot, IncidentSummary
from contracts.hypothesis.schemas import HypothesisSet, RankedHypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
    StopDecision,
)
from investigation.graph import (
    NODE_NAMES,
    Clock,
    CollectionService,
    ContextBuilder,
    GraphDependencies,
    HypothesisService,
    MissingInformationService,
    ProgressSink,
    QueryPlanningService,
    RankingService,
    StoppingService,
    build_investigation_graph,
)
from investigation.graph.ports import RemediationPlanningService


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class MockRemediationStep(BaseModel):
    step_number: int = 1
    title: str = "Roll back configuration"
    purpose: str = "Revert anomalous pool size setting"
    instructions: list[str] = ["Access configuration console", "Revert max_pool_size to 50"]
    expected_result: str = "Connection count normalizes"
    verification: list[str] = ["Check active connections metric"]
    rollback_guidance: list[str] = ["Restore previous setting if errors persist"]
    requires_human_approval: bool = True


class MockRemediationPlan(BaseModel):
    plan_id: str = "plan-int-1"
    incident_id: str = "inc-int-1"
    created_at: datetime = Field(default_factory=lambda: NOW)
    recommendation_available: bool = True
    safety_notice: str = "Human operator review is strictly required before taking any action."
    hypothesis_id: str | None = "hyp-1"
    root_cause_category: str | None = "configuration"
    confidence: str | None = "high"
    evidence_ids: list[str] = Field(default_factory=lambda: ["ev-1"])
    risk: str = "low"
    prerequisites: list[str] = Field(default_factory=lambda: ["Verify operator access"])
    steps: list[MockRemediationStep] = Field(default_factory=lambda: [MockRemediationStep()])
    escalation_guidance: list[str] = Field(default_factory=list)
    unresolved_uncertainty: list[str] = Field(default_factory=list)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingProgressSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class ScriptedCollectionService:
    async def collect(self, plan, capabilities):
        return RawEvidenceBatch(
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            batch_id="batch-1",
            collected_at=NOW,
            results=[
                SourceResult(
                    query_id=plan.queries[0].query_id,
                    source_type=SourceType.LOGS,
                    source_adapter="test-logs",
                    source_status=SourceStatus.EMPTY,
                    started_at=NOW,
                    completed_at=NOW,
                )
            ],
        )


class ScriptedContextBuilder:
    async def build(self, incident, batch, previous_context):
        return previous_context.model_copy(
            update={
                "snapshot_id": f"ctx-{incident.incident_id}-1",
                "revision": 1,
                "created_at": NOW,
                "source_coverage": {
                    **previous_context.source_coverage,
                    SourceType.LOGS.value: SourceCoverageStatus.EMPTY,
                },
            }
        )


class ScriptedMissingInformationService:
    async def assess(self, incident, capabilities, context, hypotheses, previous_assessment):
        return MissingInformationAssessment(
            incident_id=incident.incident_id,
            assessment_id="assessment-1",
            generated_at=NOW,
        )


class ScriptedQueryPlanningService:
    async def plan(self, assessment, capabilities, context, hypotheses, budget, budget_usage, round_number, query_history):
        return EvidenceQueryPlan(
            incident_id=assessment.incident_id,
            plan_id=f"plan-{round_number}",
            round_number=round_number,
            queries=[
                EvidenceQuery(
                    query_id=f"query-{round_number}",
                    source_type=SourceType.LOGS,
                    question="Collect application errors.",
                )
            ],
        )


class ScriptedHypothesisService:
    async def generate(self, incident, context, budget):
        return HypothesisSet(
            incident_id=incident.incident_id,
            hypotheses=[],
            generated_at=NOW,
        )

    async def revise(self, incident, previous_hypotheses, context, assessment):
        return previous_hypotheses.model_copy(update={"revision": 2})


class RankStoppingService:
    def evaluate(self, context, hypotheses, assessment, query_plan, budget, budget_usage, round_number):
        return StopDecision(
            action=StopAction.RANK,
            reason="Scripted evidence threshold reached.",
            stop_reason=StopReason.SUFFICIENT_EVIDENCE,
        )


class InconclusiveStoppingService:
    def evaluate(self, context, hypotheses, assessment, query_plan, budget, budget_usage, round_number):
        return StopDecision(
            action=StopAction.INCONCLUSIVE,
            reason="Budget exhausted with insufficient evidence.",
            stop_reason=StopReason.BUDGET_EXHAUSTED,
        )


class ScriptedRankingService:
    def rank(self, hypotheses, context, budget_usage, status, stop_reason, remaining_uncertainty):
        return RankedHypothesisSet(
            incident_id=context.incident_id,
            context_snapshot_id=context.snapshot_id,
            ranking_id="ranking-1",
            created_at=NOW,
            status=status,
            hypotheses=[],
            remaining_uncertainty=remaining_uncertainty,
            stop_reason=stop_reason,
            budget_usage=budget_usage,
        )


class ScriptedRemediationPlanningService:
    def __init__(self, plan: MockRemediationPlan | None = None) -> None:
        self.call_count = 0
        self.plan_to_return = plan or MockRemediationPlan()

    async def plan(self, ranked, context):
        self.call_count += 1
        return self.plan_to_return


def sample_dependencies(
    stopping_service=None,
    remediation_plan=None,
) -> tuple[GraphDependencies, ScriptedRemediationPlanningService, RecordingProgressSink]:
    sink = RecordingProgressSink()
    remediation_service = ScriptedRemediationPlanningService(remediation_plan)
    deps = GraphDependencies(
        collection_service=ScriptedCollectionService(),
        context_builder=ScriptedContextBuilder(),
        missing_information_service=ScriptedMissingInformationService(),
        query_planning_service=ScriptedQueryPlanningService(),
        hypothesis_service=ScriptedHypothesisService(),
        stopping_service=stopping_service or RankStoppingService(),
        ranking_service=ScriptedRankingService(),
        progress_sink=sink,
        clock=FixedClock(),
        remediation_planning_service=remediation_service,
    )
    return deps, remediation_service, sink


def sample_input(incident_id: str = "inc-int-1") -> dict[str, object]:
    incident = IncidentSeed(
        incident_id=incident_id,
        external_alert_id="alert-1",
        service="payments",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=NOW,
        received_at=NOW,
        summary="High payment processing error rate.",
    )
    capabilities = SourceCapabilityCatalog(
        incident_id=incident.incident_id,
        generated_at=NOW,
        sources=[
            SourceCapability(
                source_type=source_type,
                available=source_type == SourceType.LOGS,
                adapter_name="test-logs" if source_type == SourceType.LOGS else "unregistered",
                unavailable_reason=None if source_type == SourceType.LOGS else "Not configured",
            )
            for source_type in SourceType
        ],
    )
    return {
        "incident": incident,
        "source_capabilities": capabilities,
        "budget": InvestigationBudget(),
    }


def test_remediation_node_in_graph_topology() -> None:
    deps, _, _ = sample_dependencies()
    graph = build_investigation_graph(deps).get_graph()

    assert "plan_remediation" in NODE_NAMES
    assert "plan_remediation" in graph.nodes
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("rank_hypotheses", "plan_remediation") in edges
    assert ("finish_inconclusive", "plan_remediation") in edges
    assert ("plan_remediation", "__end__") in edges


@pytest.mark.asyncio
async def test_completed_investigation_routes_to_plan_remediation() -> None:
    expected_plan = MockRemediationPlan(
        recommendation_available=True,
        risk="low",
        safety_notice="Human review mandatory for config rollbacks.",
    )
    deps, planner, sink = sample_dependencies(remediation_plan=expected_plan)
    graph = build_investigation_graph(deps)

    result = await graph.ainvoke(sample_input())

    assert planner.call_count == 1
    assert result["remediation_plan"] is expected_plan
    assert result["remediation_plan"].recommendation_available is True

    # Progress events include rank_hypotheses then plan_remediation
    stages = [event.stage for event in result["progress_events"]]
    assert "rank_hypotheses" in stages
    assert "plan_remediation" in stages
    assert stages[-1] == "plan_remediation"


@pytest.mark.asyncio
async def test_inconclusive_investigation_routes_to_plan_remediation() -> None:
    blocked_plan = MockRemediationPlan(
        recommendation_available=False,
        risk="blocked",
        steps=[],
        escalation_guidance=["Escalate to network infrastructure team."],
        unresolved_uncertainty=["Logs inconclusive on packet loss."],
        safety_notice="No automated change recommended. Operator escalation required.",
    )
    deps, planner, sink = sample_dependencies(
        stopping_service=InconclusiveStoppingService(),
        remediation_plan=blocked_plan,
    )
    graph = build_investigation_graph(deps)

    result = await graph.ainvoke(sample_input())

    assert planner.call_count == 1
    assert result["ranked_result"].status == InvestigationStatus.INCONCLUSIVE
    assert result["remediation_plan"] is blocked_plan
    assert result["remediation_plan"].recommendation_available is False
    assert result["remediation_plan"].risk == "blocked"

    stages = [event.stage for event in result["progress_events"]]
    assert "finish_inconclusive" in stages
    assert "plan_remediation" in stages
    assert stages[-1] == "plan_remediation"


@pytest.mark.asyncio
async def test_checkpoint_preserves_remediation_plan_on_resume() -> None:
    checkpointer = InMemorySaver()
    expected_plan = MockRemediationPlan(plan_id="plan-checkpoint-42")
    deps, planner, _ = sample_dependencies(remediation_plan=expected_plan)
    graph = build_investigation_graph(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "inc-checkpoint-1"}}

    first_result = await graph.ainvoke(sample_input("inc-checkpoint-1"), config=config)
    assert first_result["remediation_plan"].plan_id == "plan-checkpoint-42"
    assert planner.call_count == 1

    # Resume from checkpoint
    snapshot = graph.get_state(config)
    assert snapshot.values["remediation_plan"] is not None
    assert snapshot.values["remediation_plan"].plan_id == "plan-checkpoint-42"
    assert snapshot.values["workflow_state"] == InvestigationState.COMPLETED
