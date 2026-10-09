"""Topology, protocol, and execution tests for the investigation graph."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

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
    SourceStatus,
    SourceType,
    StopAction,
    StopReason,
)
from contracts.evidence.schemas import IncidentContextSnapshot
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
    route_after_evaluation,
    route_after_plan,
)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


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
                    SourceType.LOGS.value: "empty",
                },
            }
        )


class ScriptedMissingInformationService:
    async def assess(
        self,
        incident,
        capabilities,
        context,
        hypotheses,
        previous_assessment,
    ):
        return MissingInformationAssessment(
            incident_id=incident.incident_id,
            assessment_id="assessment-1",
            generated_at=NOW,
        )


class ScriptedQueryPlanningService:
    async def plan(
        self,
        assessment,
        capabilities,
        context,
        hypotheses,
        budget,
        budget_usage,
        round_number,
        query_history,
    ):
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
    def evaluate(
        self,
        context,
        hypotheses,
        assessment,
        query_plan,
        budget,
        budget_usage,
        round_number,
    ):
        return StopDecision(
            action=StopAction.RANK,
            reason="Scripted evidence threshold reached.",
            stop_reason=StopReason.SUFFICIENT_EVIDENCE,
        )


class ScriptedRankingService:
    def rank(
        self,
        hypotheses,
        context,
        budget_usage,
        status,
        stop_reason,
        remaining_uncertainty,
    ):
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


def dependencies() -> GraphDependencies:
    return GraphDependencies(
        collection_service=ScriptedCollectionService(),
        context_builder=ScriptedContextBuilder(),
        missing_information_service=ScriptedMissingInformationService(),
        query_planning_service=ScriptedQueryPlanningService(),
        hypothesis_service=ScriptedHypothesisService(),
        stopping_service=RankStoppingService(),
        ranking_service=ScriptedRankingService(),
        progress_sink=RecordingProgressSink(),
        clock=FixedClock(),
    )


def invocation_input() -> dict[str, object]:
    incident = IncidentSeed(
        incident_id="inc-1",
        external_alert_id="alert-1",
        service="payments",
        environment="test",
        severity=Severity.CRITICAL,
        detected_at=NOW,
        received_at=NOW,
        summary="Error rate increased.",
    )
    capabilities = SourceCapabilityCatalog(
        incident_id=incident.incident_id,
        generated_at=NOW,
        sources=[
            SourceCapability(
                source_type=source_type,
                available=source_type == SourceType.LOGS,
                adapter_name=(
                    "test-logs"
                    if source_type == SourceType.LOGS
                    else "unregistered"
                ),
                unavailable_reason=(
                    None
                    if source_type == SourceType.LOGS
                    else "Not configured for test."
                ),
            )
            for source_type in SourceType
        ],
    )
    return {
        "incident": incident,
        "source_capabilities": capabilities,
        "budget": InvestigationBudget(),
    }


def test_scripted_services_conform_to_frozen_protocols() -> None:
    deps = dependencies()
    assert isinstance(deps.collection_service, CollectionService)
    assert isinstance(deps.context_builder, ContextBuilder)
    assert isinstance(deps.missing_information_service, MissingInformationService)
    assert isinstance(deps.query_planning_service, QueryPlanningService)
    assert isinstance(deps.hypothesis_service, HypothesisService)
    assert isinstance(deps.stopping_service, StoppingService)
    assert isinstance(deps.ranking_service, RankingService)
    assert isinstance(deps.progress_sink, ProgressSink)
    assert isinstance(deps.clock, Clock)


def test_graph_topology_keeps_the_approved_nodes_and_edges() -> None:
    graph = build_investigation_graph(dependencies()).get_graph()
    assert set(NODE_NAMES).issubset(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("__start__", "initialize") in edges
    assert ("evaluate_stopping", "assess_gaps") in edges
    assert ("evaluate_stopping", "rank_hypotheses") in edges
    assert ("evaluate_stopping", "finish_inconclusive") in edges
    assert ("rank_hypotheses", "__end__") in edges


def test_local_sqlite_checkpointer_dependency_is_available() -> None:
    with SqliteSaver.from_conn_string(":memory:") as checkpointer:
        assert checkpointer is not None


def test_routing_reads_only_the_frozen_decision_fields() -> None:
    stop_plan = EvidenceQueryPlan(
        incident_id="inc-1",
        plan_id="plan-stop",
        round_number=1,
        stop_reason=StopReason.SOURCES_UNAVAILABLE,
    )
    assert route_after_plan({"query_plan": stop_plan}) == "finish_inconclusive"
    assert route_after_evaluation(
        {
            "stop_decision": StopDecision(
                action=StopAction.CONTINUE,
                reason="More evidence is useful.",
            )
        }
    ) == "assess_gaps"


@pytest.mark.asyncio
async def test_compiled_graph_executes_the_rank_path() -> None:
    deps = dependencies()
    graph = build_investigation_graph(deps)
    result = await graph.ainvoke(invocation_input())

    assert result["ranked_result"].status == InvestigationStatus.COMPLETED
    assert result["ranked_result"].stop_reason == StopReason.SUFFICIENT_EVIDENCE
    assert result["context"].revision == 1
    assert len(result["query_history"]) == 1
    assert len(result["batch_history"]) == 1
    assert result["errors"] == []
    assert [event.stage for event in result["progress_events"]] == [
        "initialize",
        "assess_gaps",
        "plan_queries",
        "collect_evidence",
        "build_context",
        "hypothesize",
        "evaluate_stopping",
        "rank_hypotheses",
    ]
    assert result["ranked_result"].budget_usage == BudgetUsage(
        rounds=1,
        queries=1,
        reasoning_calls=2,
    )
    assert len(deps.progress_sink.events) == 8


@pytest.mark.asyncio
async def test_checkpoint_state_uses_incident_id_as_thread_id() -> None:
    checkpointer = InMemorySaver()
    graph = build_investigation_graph(dependencies(), checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "inc-1"}}

    await graph.ainvoke(invocation_input(), config=config)
    snapshot = graph.get_state(config)

    assert snapshot.values["incident"].incident_id == "inc-1"
    assert snapshot.values["workflow_state"] == InvestigationState.COMPLETED
    assert snapshot.values["ranked_result"].incident_id == "inc-1"
