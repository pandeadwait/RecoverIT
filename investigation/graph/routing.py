"""Pure conditional-edge routing for the investigation graph."""

from __future__ import annotations

from typing import Literal

from contracts.enums import StopAction
from investigation.graph.state import InvestigationGraphState


def route_after_plan(
    state: InvestigationGraphState,
) -> Literal["collect_evidence", "finish_inconclusive"]:
    plan = state.get("query_plan")
    if plan is None:
        raise ValueError("plan_queries must set query_plan before routing")
    return "collect_evidence" if plan.queries else "finish_inconclusive"


def route_after_evaluation(
    state: InvestigationGraphState,
) -> Literal["assess_gaps", "rank_hypotheses", "finish_inconclusive"]:
    decision = state.get("stop_decision")
    if decision is None:
        raise ValueError("evaluate_stopping must set stop_decision before routing")
    if decision.action == StopAction.CONTINUE:
        return "assess_gaps"
    if decision.action == StopAction.RANK:
        return "rank_hypotheses"
    return "finish_inconclusive"


__all__ = ["route_after_evaluation", "route_after_plan"]
