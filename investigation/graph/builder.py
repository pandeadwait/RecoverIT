"""Build the approved RecoverIT investigation workflow with LangGraph."""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from investigation.graph.dependencies import GraphDependencies
from investigation.graph.nodes import (
    assess_gaps,
    build_context,
    collect_evidence,
    evaluate_stopping,
    finish_inconclusive,
    hypothesize,
    initialize,
    plan_queries,
    rank_hypotheses,
)
from investigation.graph.routing import route_after_evaluation, route_after_plan
from investigation.graph.state import (
    InvestigationGraphState,
    InvestigationInput,
    InvestigationOutput,
)


NODE_NAMES = (
    "initialize",
    "assess_gaps",
    "plan_queries",
    "collect_evidence",
    "build_context",
    "hypothesize",
    "evaluate_stopping",
    "rank_hypotheses",
    "finish_inconclusive",
)


def build_investigation_graph(
    dependencies: GraphDependencies,
    checkpointer: BaseCheckpointSaver | bool | None = None,
) -> CompiledStateGraph:
    """Compile the mentor-approved bounded investigation graph."""

    builder = StateGraph(
        InvestigationGraphState,
        input_schema=InvestigationInput,
        output_schema=InvestigationOutput,
    )
    builder.add_node("initialize", partial(initialize, dependencies=dependencies))
    builder.add_node("assess_gaps", partial(assess_gaps, dependencies=dependencies))
    builder.add_node("plan_queries", partial(plan_queries, dependencies=dependencies))
    builder.add_node(
        "collect_evidence", partial(collect_evidence, dependencies=dependencies)
    )
    builder.add_node("build_context", partial(build_context, dependencies=dependencies))
    builder.add_node("hypothesize", partial(hypothesize, dependencies=dependencies))
    builder.add_node(
        "evaluate_stopping", partial(evaluate_stopping, dependencies=dependencies)
    )
    builder.add_node(
        "rank_hypotheses", partial(rank_hypotheses, dependencies=dependencies)
    )
    builder.add_node(
        "finish_inconclusive", partial(finish_inconclusive, dependencies=dependencies)
    )

    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "assess_gaps")
    builder.add_edge("assess_gaps", "plan_queries")
    builder.add_conditional_edges("plan_queries", route_after_plan)
    builder.add_edge("collect_evidence", "build_context")
    builder.add_edge("build_context", "hypothesize")
    builder.add_edge("hypothesize", "evaluate_stopping")
    builder.add_conditional_edges("evaluate_stopping", route_after_evaluation)
    builder.add_edge("rank_hypotheses", END)
    builder.add_edge("finish_inconclusive", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["NODE_NAMES", "build_investigation_graph"]
