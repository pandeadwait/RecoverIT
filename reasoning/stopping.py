"""Deterministic investigation stopping service.

Evaluates multi-source evidence sufficiency, causal and symptom citations,
confidence thresholds, margin over rank #2, and budget constraints.
Returns canonical StopDecision contracts consumed by the LangGraph router.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.common import (
    EvidenceRole,
    InformationPriority,
    RootCauseCategory,
    SourceType,
    StopAction,
    StopReason,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import HypothesisSet
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
    StopDecision,
)
from reasoning.ranking.ranking_engine import RankingEngine

logger = logging.getLogger(__name__)

CHANGE_CATEGORIES = frozenset({
    RootCauseCategory.CONFIGURATION_REGRESSION,
    RootCauseCategory.DEPLOYMENT_FAILURE,
    RootCauseCategory.CODE_DEFECT,
    "configuration_regression",
    "deployment_failure",
    "code_defect",
})

CAUSAL_SOURCES = frozenset({
    SourceType.CHANGES,
    SourceType.CONFIGURATION,
    SourceType.DEPLOYMENTS,
    SourceType.PIPELINES,
})

SYMPTOM_SOURCES = frozenset({
    SourceType.LOGS,
    SourceType.METRICS,
    SourceType.HEALTH,
})


class ExtendedStopDecision(StopDecision):
    """Canonical StopDecision with convenience compatibility properties."""

    @property
    def should_stop(self) -> bool:
        return self.action != StopAction.CONTINUE

    @property
    def is_inconclusive(self) -> bool:
        return self.action == StopAction.INCONCLUSIVE


class DefaultStoppingService:
    """Deterministic stopping rule evaluator conforming to StoppingService protocol."""

    def __init__(
        self,
        min_supporting_sources_for_adequate: int = 2,
        min_evidence_score_for_confidence: float = 50.0,
        min_score_margin: float = 5.0,
        require_causal_evidence: bool = True,
        require_symptom_evidence: bool = True,
        require_no_high_priority_gaps: bool = True,
        require_alternative_tested: bool = True,
        ranking_engine: RankingEngine | None = None,
    ) -> None:
        self.min_supporting_sources_for_adequate = min_supporting_sources_for_adequate
        self.min_evidence_score_for_confidence = min_evidence_score_for_confidence
        self.min_score_margin = min_score_margin
        self.require_causal_evidence = require_causal_evidence
        self.require_symptom_evidence = require_symptom_evidence
        self.require_no_high_priority_gaps = require_no_high_priority_gaps
        self.require_alternative_tested = require_alternative_tested
        self._ranking_engine = ranking_engine or RankingEngine()

    def evaluate_completion_criteria(
        self,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        missing_info: MissingInformationAssessment | None = None,
    ) -> tuple[bool, dict[str, bool], list[str]]:
        """Evaluate completion requirements against current context and hypotheses."""
        criteria: dict[str, bool] = {}
        unresolved: list[str] = []

        if not hypotheses or not hypotheses.hypotheses or not context.evidence:
            return False, {"has_hypotheses": False}, ["No active hypotheses or evidence available."]

        context_ids = {e.evidence_id: e for e in context.evidence}

        ranked = self._ranking_engine.rank(hypotheses, context)
        if not ranked.hypotheses:
            return False, {"has_ranked": False}, ["No hypotheses could be ranked."]

        leading = ranked.hypotheses[0]
        valid_citations = [
            c for c in leading.supporting_evidence if c.evidence_id in context_ids
        ]

        # 1. Independent sources
        cited_sources = {
            context_ids[c.evidence_id].source_type
            for c in valid_citations
        }
        has_adequate_sources = len(cited_sources) >= self.min_supporting_sources_for_adequate
        criteria["independent_sources"] = has_adequate_sources
        if not has_adequate_sources:
            unresolved.append(
                f"Needs {self.min_supporting_sources_for_adequate} independent sources "
                f"(currently {len(cited_sources)}: {', '.join(str(getattr(s, 'value', s)) for s in cited_sources) or 'none'})"
            )

        if self.min_supporting_sources_for_adequate <= 1:
            is_complete = has_adequate_sources and len(valid_citations) > 0
            return is_complete, criteria, unresolved

        # 2. Direct causal record
        has_causal = False
        cat = str(getattr(leading.root_cause_category, "value", leading.root_cause_category))
        if cat in CHANGE_CATEGORIES:
            for c in valid_citations:
                st = context_ids[c.evidence_id].source_type
                if c.role == EvidenceRole.CAUSE or st in CAUSAL_SOURCES:
                    has_causal = True
                    break
        else:
            for c in valid_citations:
                st = context_ids[c.evidence_id].source_type
                if c.role == EvidenceRole.CAUSE or st not in SYMPTOM_SOURCES:
                    has_causal = True
                    break
            if not has_causal and valid_citations:
                has_causal = True

        criteria["causal_evidence"] = has_causal if self.require_causal_evidence else True
        if self.require_causal_evidence and not has_causal:
            unresolved.append("Missing direct causal evidence (change/commit/config record)")

        # 3. Symptom or impact record
        has_symptom = False
        for c in valid_citations:
            st = context_ids[c.evidence_id].source_type
            if c.role == EvidenceRole.EFFECT or st in SYMPTOM_SOURCES:
                has_symptom = True
                break
        if not has_symptom and any(e.source_type in SYMPTOM_SOURCES for e in context.evidence):
            has_symptom = True

        criteria["symptom_evidence"] = has_symptom if self.require_symptom_evidence else True
        if self.require_symptom_evidence and not has_symptom:
            unresolved.append("Missing symptom/impact corroboration (logs or metrics)")

        # 4. No unresolved HIGH-priority information gaps
        has_high_gaps = False
        unresolved_high = []
        if missing_info and missing_info.missing_information:
            for g in missing_info.missing_information:
                if g.priority == InformationPriority.HIGH and not g.resolved:
                    if g.candidate_sources and any(e.source_type in g.candidate_sources for e in context.evidence):
                        continue
                    unresolved_high.append(g)
            has_high_gaps = len(unresolved_high) > 0

        criteria["no_high_priority_gaps"] = (not has_high_gaps) if self.require_no_high_priority_gaps else True
        if self.require_no_high_priority_gaps and has_high_gaps:
            unresolved.append(f"{len(unresolved_high)} unresolved HIGH-priority information gap(s)")

        # 5. Leading hypothesis exceeds confidence threshold
        score_ok = leading.evidence_score >= self.min_evidence_score_for_confidence
        criteria["confidence_threshold"] = score_ok
        if not score_ok:
            unresolved.append(
                f"Evidence score {leading.evidence_score:.1f} below threshold {self.min_evidence_score_for_confidence:.1f}"
            )

        # 6. Score margin over rank two
        margin_ok = True
        margin = 0.0
        if len(ranked.hypotheses) > 1:
            margin = leading.evidence_score - ranked.hypotheses[1].evidence_score
            margin_ok = margin >= self.min_score_margin
        criteria["score_margin"] = margin_ok
        if not margin_ok:
            unresolved.append(
                f"Score margin {margin:.1f} over rank #2 below required margin {self.min_score_margin:.1f}"
            )

        # 7. Alternative explanation tested
        has_alternative = len(hypotheses.hypotheses) >= 2
        criteria["alternative_tested"] = has_alternative if self.require_alternative_tested else True
        if self.require_alternative_tested and not has_alternative:
            unresolved.append("Fewer than 2 competing explanations evaluated")

        is_complete = all(criteria.values())
        return is_complete, criteria, unresolved

    def evaluate(
        self,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None = None,
        assessment: MissingInformationAssessment | None = None,
        query_plan: Any | None = None,
        budget: InvestigationBudget | None = None,
        budget_usage: BudgetUsage | None = None,
        round_number: int = 1,
        *,
        round_num: int | None = None,
        missing_info: MissingInformationAssessment | None = None,
        max_rounds: int | None = None,
        budget_tracker: Any = None,
        **kwargs: Any,
    ) -> StopDecision:
        """Evaluate stopping criteria and return canonical StopDecision."""
        eff_round = round_number if round_number is not None else (round_num or 1)
        eff_assessment = assessment or missing_info
        eff_budget = budget or InvestigationBudget()
        eff_usage = budget_usage or BudgetUsage()
        limit_rounds = max_rounds or eff_budget.max_rounds

        # 1. Check hypothesis sufficiency
        if hypotheses is not None and len(hypotheses.hypotheses) == 0:
            return ExtendedStopDecision(
                action=StopAction.INCONCLUSIVE,
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                reason="Fewer than the minimum hypotheses generated.",
                criteria_status={"has_hypotheses": False},
                unresolved_criteria=["Zero hypotheses generated."],
            )

        # 2. Check source unavailability from query plan
        if query_plan is not None and getattr(query_plan, "stop_reason", None) == StopReason.SOURCES_UNAVAILABLE:
            return ExtendedStopDecision(
                action=StopAction.INCONCLUSIVE,
                stop_reason=StopReason.SOURCES_UNAVAILABLE,
                reason="Required sources unavailable to resolve information gaps.",
                criteria_status={"sources_available": False},
                unresolved_criteria=["All candidate data sources are unavailable."],
            )

        # 3. Check for zero valid evidence in context
        if not context.evidence:
            # If round 1 and queries were planned, we continue to gather evidence
            if eff_round < limit_rounds and query_plan is not None and getattr(query_plan, "queries", []):
                return ExtendedStopDecision(
                    action=StopAction.CONTINUE,
                    stop_reason=None,
                    reason=f"Continuing to round {eff_round + 1} to collect initial evidence.",
                    criteria_status={"has_evidence": False},
                    unresolved_criteria=["Initial evidence not yet collected."],
                )
            return ExtendedStopDecision(
                action=StopAction.INCONCLUSIVE,
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                reason="Zero valid evidence collected in incident context.",
                criteria_status={"has_evidence": False},
                unresolved_criteria=["No evidence available in incident context."],
            )

        # 4. Evaluate completion criteria
        is_complete, criteria_status, unresolved = self.evaluate_completion_criteria(
            context=context,
            hypotheses=hypotheses,
            missing_info=eff_assessment,
        )

        if is_complete:
            return ExtendedStopDecision(
                action=StopAction.RANK,
                stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                reason="All completion criteria met; sufficient evidence gathered to support diagnosis.",
                criteria_status=criteria_status,
                unresolved_criteria=[],
            )

        # 5. Check budget exhaustion
        is_exhausted = False
        exhaust_reason = ""
        if budget_tracker is not None and getattr(budget_tracker, "is_budget_exhausted", lambda: False)():
            is_exhausted = True
            exhaust_reason = "Budget tracker reported exhausted limits."
        elif eff_round >= limit_rounds or eff_usage.rounds >= limit_rounds:
            is_exhausted = True
            exhaust_reason = f"Maximum rounds reached ({limit_rounds})."
        elif eff_usage.queries >= eff_budget.max_queries:
            is_exhausted = True
            exhaust_reason = f"Maximum queries reached ({eff_budget.max_queries})."
        elif query_plan is not None and getattr(query_plan, "stop_reason", None) == StopReason.BUDGET_EXHAUSTED:
            is_exhausted = True
            exhaust_reason = "Query plan reported budget exhausted."
        elif eff_usage.reasoning_calls >= eff_budget.max_reasoning_calls:
            is_exhausted = True
            exhaust_reason = f"Maximum reasoning calls reached ({eff_budget.max_reasoning_calls})."

        if is_exhausted:
            return ExtendedStopDecision(
                action=StopAction.INCONCLUSIVE,
                stop_reason=StopReason.BUDGET_EXHAUSTED,
                reason=f"Investigation stopped: budget exhausted before all criteria were satisfied. {exhaust_reason}",
                criteria_status=criteria_status,
                unresolved_criteria=unresolved,
            )

        # 6. Check if plan cannot yield further queries
        if query_plan is not None and getattr(query_plan, "stop_reason", None) == StopReason.INSUFFICIENT_EVIDENCE:
            return ExtendedStopDecision(
                action=StopAction.INCONCLUSIVE,
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                reason="No further evidence queries can be planned.",
                criteria_status=criteria_status,
                unresolved_criteria=unresolved,
            )

        # 7. Default: continue investigation
        unresolved_summary = "; ".join(unresolved[:2])
        return ExtendedStopDecision(
            action=StopAction.CONTINUE,
            stop_reason=None,
            reason=f"Continuing to round {eff_round + 1}. Unresolved: {unresolved_summary}",
            criteria_status=criteria_status,
            unresolved_criteria=unresolved,
        )


# Compatibility alias
StoppingRuleEvaluator = DefaultStoppingService
