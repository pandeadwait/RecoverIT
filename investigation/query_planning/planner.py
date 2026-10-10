"""
Evidence query planner component.

Translates missing information items into concrete evidence queries
constrained by source capabilities, time windows, item limits, and budgets.
Rejects invalid or duplicate queries with structured warnings.

See WORK_DIVISION.md §8.5, §8.9 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from contracts.collection.schemas import SourceCapability, SourceCapabilityCatalog
from contracts.common import (
    InformationGapCategory,
    InformationValueLevel,
    SourceType,
    StopReason,
)
from contracts.errors.schemas import (
    BUDGET_EXHAUSTED,
    DUPLICATE_QUERY,
    INVALID_QUERY,
    SOURCE_UNAVAILABLE,
    StructuredError,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import HypothesisSet
from contracts.investigation.schemas import (
    BudgetUsage,
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    MissingInformationAssessment,
)
from investigation.budgets.budget_tracker import BudgetTracker
from reasoning.provider.interface import ReasoningProvider

logger = logging.getLogger(__name__)


class EvidenceQueryPlanner:
    """
    Plans and validates evidence queries for an investigation round.

    Guarantees:
    - Queries use only capabilities advertised in SourceCapabilityCatalog.
    - Parameters strictly adhere to supported_query_fields.
    - Time windows do not exceed maximum_window_seconds.
    - limit does not exceed maximum_items.
    - Duplicate queries (in-plan or historical) are rejected.
    - Budget limits are enforced (cannot exceed remaining queries).
    - Plans with zero queries always provide a stop_reason.
    - Invalid queries are filtered out with structured warnings.
    """

    def __init__(
        self,
        provider: ReasoningProvider,
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self._provider = provider
        self._budget_tracker = budget_tracker
        self._last_warnings: list[StructuredError] = []

    @property
    def provider(self) -> ReasoningProvider:
        """The underlying reasoning provider."""
        return self._provider

    @property
    def budget_tracker(self) -> BudgetTracker | None:
        """The optional budget tracker."""
        return self._budget_tracker

    @property
    def last_warnings(self) -> list[StructuredError]:
        """Structured warnings generated during the last planning run."""
        return list(self._last_warnings)

    async def plan(
        self,
        assessment: MissingInformationAssessment | None = None,
        capabilities: SourceCapabilityCatalog | None = None,
        context: IncidentContextSnapshot | None = None,
        hypotheses: HypothesisSet | None = None,
        budget: InvestigationBudget | None = None,
        budget_usage: BudgetUsage | None = None,
        round_number: int | None = None,
        query_history: list[EvidenceQueryPlan] | None = None,
        *,
        missing_information: MissingInformationAssessment | None = None,
        source_capabilities: SourceCapabilityCatalog | None = None,
        round_num: int = 1,
        history_queries: list[EvidenceQueryPlanQuery] | None = None,
        **kwargs: Any,
    ) -> EvidenceQueryPlan:
        """
        Produce a validated EvidenceQueryPlan constrained by capabilities and budget.
        Satisfies the frozen QueryPlanningService protocol.
        """
        self._last_warnings.clear()

        eff_assessment = assessment if assessment is not None else missing_information
        if eff_assessment is None:
            raise ValueError("assessment must be provided")

        eff_capabilities = capabilities if capabilities is not None else source_capabilities
        if eff_capabilities is None:
            raise ValueError("capabilities must be provided")

        if context is None:
            raise ValueError("context must be provided")

        if budget is None:
            budget = InvestigationBudget()

        eff_round = round_number if round_number is not None else round_num

        historical_queries: list[EvidenceQueryPlanQuery] = []
        if query_history:
            for hp in query_history:
                historical_queries.extend(hp.queries)
        if history_queries:
            historical_queries.extend(history_queries)

        # Check if stopping is already recommended or if budget is already exhausted
        is_exhausted = False
        if self._budget_tracker and self._budget_tracker.is_budget_exhausted():
            is_exhausted = True
        elif budget_usage is not None and budget_usage.queries >= budget.max_queries:
            is_exhausted = True

        if is_exhausted:
            return EvidenceQueryPlan(
                incident_id=eff_assessment.incident_id,
                plan_id=f"plan_{eff_assessment.incident_id}_{eff_round}",
                round_number=eff_round,
                queries=[],
                stop_reason=StopReason.BUDGET_EXHAUSTED,
            )

        if eff_assessment.recommended_stop or not eff_assessment.missing_information:
            stop = (
                StopReason.SOURCES_UNAVAILABLE
                if eff_assessment.unavailable_information and not eff_assessment.missing_information
                else StopReason.SUFFICIENT_EVIDENCE
            )
            return EvidenceQueryPlan(
                incident_id=eff_assessment.incident_id,
                plan_id=f"plan_{eff_assessment.incident_id}_{eff_round}",
                round_number=eff_round,
                queries=[],
                stop_reason=stop,
            )

        # Call provider for raw query plan
        raw_plan = await self._provider.plan_queries(
            missing_information=eff_assessment,
            source_capabilities=eff_capabilities,
            context=context,
            budget=budget,
        )

        return self.validate_plan(
            plan=raw_plan,
            source_capabilities=eff_capabilities,
            budget=budget,
            round_num=eff_round,
            history_queries=historical_queries,
            missing_information=eff_assessment,
            budget_usage=budget_usage,
        )

    def validate_plan(
        self,
        plan: EvidenceQueryPlan,
        source_capabilities: SourceCapabilityCatalog,
        budget: InvestigationBudget,
        round_num: int = 1,
        history_queries: list[EvidenceQueryPlanQuery] | None = None,
        missing_information: MissingInformationAssessment | None = None,
        budget_usage: BudgetUsage | None = None,
    ) -> EvidenceQueryPlan:
        """
        Validate and filter candidate queries against catalog capabilities and budget limits.
        """
        catalog_map: dict[SourceType, SourceCapability] = {
            s.source_type: s for s in source_capabilities.sources
        }

        seen_queries: list[tuple[SourceType, str]] = []
        if history_queries:
            for hq in history_queries:
                seen_queries.append((hq.source_type, self._canonical_params(hq.parameters)))

        valid_queries: list[EvidenceQueryPlanQuery] = []

        for q in plan.queries:
            warning = self._validate_single_query(q, catalog_map, seen_queries)
            if warning is not None:
                self._last_warnings.append(warning)
                continue

            # Ensure related_information_ids are populated if empty
            if missing_information and not q.related_information_ids:
                matching_gap_ids = [
                    item.information_id
                    for item in missing_information.missing_information
                    if q.source_type in item.candidate_sources
                ]
                if matching_gap_ids:
                    q = q.model_copy(update={"related_information_ids": matching_gap_ids})

            param_canonical = self._canonical_params(q.parameters)
            seen_queries.append((q.source_type, param_canonical))
            valid_queries.append(q)

        # Prioritize direct causal evidence queries when change-related gaps exist
        has_direct_causal_gap = any(
            getattr(item, "category", None) == InformationGapCategory.DIRECT_CAUSAL_EVIDENCE
            for item in (missing_information.missing_information if missing_information else [])
        )
        if has_direct_causal_gap:
            causal_types = {
                SourceType.CHANGES,
                SourceType.CONFIGURATION,
                SourceType.DEPLOYMENTS,
                SourceType.PIPELINES,
            }
            valid_queries.sort(key=lambda q: 0 if q.source_type in causal_types else 1)

        # Enforce budget limits
        max_allowed_queries: int
        if self._budget_tracker is not None:
            max_allowed_queries = int(self._budget_tracker.remaining()["queries"])
        elif budget_usage is not None:
            max_allowed_queries = max(0, budget.max_queries - budget_usage.queries)
        else:
            max_allowed_queries = budget.max_queries

        if max_allowed_queries <= 0:
            valid_queries = []
            stop_reason = StopReason.BUDGET_EXHAUSTED
            self._last_warnings.append(
                StructuredError(
                    code=BUDGET_EXHAUSTED,
                    message="Investigation query budget is exhausted.",
                    retryable=False,
                    stage="plan_queries",
                    details={"max_queries": budget.max_queries},
                )
            )
        elif len(valid_queries) > max_allowed_queries:
            dropped_count = len(valid_queries) - max_allowed_queries
            valid_queries = valid_queries[:max_allowed_queries]
            self._last_warnings.append(
                StructuredError(
                    code=BUDGET_EXHAUSTED,
                    message=(
                        f"Planned queries exceeded remaining budget. "
                        f"Truncated {dropped_count} query(ies) to stay within limit {max_allowed_queries}."
                    ),
                    retryable=False,
                    stage="plan_queries",
                    details={
                        "remaining_queries": max_allowed_queries,
                        "dropped_queries": dropped_count,
                    },
                )
            )
            stop_reason = plan.stop_reason
        else:
            stop_reason = plan.stop_reason

        # Invariant: a plan with 0 queries MUST have a stop_reason
        if len(valid_queries) == 0 and stop_reason is None:
            if max_allowed_queries <= 0:
                stop_reason = StopReason.BUDGET_EXHAUSTED
            elif missing_information and missing_information.unavailable_information and not missing_information.missing_information:
                stop_reason = StopReason.SOURCES_UNAVAILABLE
            else:
                stop_reason = StopReason.INSUFFICIENT_EVIDENCE

        return EvidenceQueryPlan(
            incident_id=plan.incident_id,
            plan_id=plan.plan_id or f"plan_{plan.incident_id}_{round_num}",
            round_number=round_num or plan.round_number,
            queries=valid_queries,
            stop_reason=stop_reason,
        )

    def _validate_single_query(
        self,
        query: EvidenceQueryPlanQuery,
        catalog_map: dict[SourceType, SourceCapability],
        seen_queries: list[tuple[SourceType, str]],
    ) -> StructuredError | None:
        # 1. Source existence and availability
        capability = catalog_map.get(query.source_type)
        if capability is None or not capability.available:
            return StructuredError(
                code=SOURCE_UNAVAILABLE,
                message=(
                    f"Source '{query.source_type}' is not available in capability catalog."
                ),
                retryable=False,
                stage="plan_queries",
                source_type=query.source_type,
                details={
                    "query_id": query.query_id,
                    "source_type": query.source_type.value if hasattr(query.source_type, "value") else str(query.source_type),
                },
            )

        # 2. Supported query fields
        supported_fields = set(capability.supported_query_fields)
        for field in query.parameters:
            if field not in supported_fields:
                return StructuredError(
                    code=INVALID_QUERY,
                    message=(
                        f"Parameter '{field}' is not supported by source '{query.source_type}'. "
                        f"Supported fields: {capability.supported_query_fields}"
                    ),
                    retryable=False,
                    stage="plan_queries",
                    source_type=query.source_type,
                    details={
                        "query_id": query.query_id,
                        "unsupported_field": field,
                        "supported_fields": capability.supported_query_fields,
                    },
                )

        # 3. Limit validation
        if "limit" in query.parameters:
            try:
                limit_val = int(query.parameters["limit"])
                if limit_val > capability.maximum_items:
                    return StructuredError(
                        code=INVALID_QUERY,
                        message=(
                            f"Query limit {limit_val} exceeds maximum_items "
                            f"({capability.maximum_items}) for source '{query.source_type}'."
                        ),
                        retryable=False,
                        stage="plan_queries",
                        source_type=query.source_type,
                        details={
                            "query_id": query.query_id,
                            "requested_limit": limit_val,
                            "maximum_items": capability.maximum_items,
                        },
                    )
            except (ValueError, TypeError):
                return StructuredError(
                    code=INVALID_QUERY,
                    message=f"Query limit '{query.parameters['limit']}' is not a valid integer.",
                    retryable=False,
                    stage="plan_queries",
                    source_type=query.source_type,
                    details={"query_id": query.query_id},
                )

        # 4. Time window validation
        window_seconds = self._extract_window_seconds(query.parameters)
        if window_seconds is not None:
            if window_seconds > capability.maximum_window_seconds:
                return StructuredError(
                    code=INVALID_QUERY,
                    message=(
                        f"Query time window ({window_seconds}s) exceeds maximum_window_seconds "
                        f"({capability.maximum_window_seconds}s) for source '{query.source_type}'."
                    ),
                    retryable=False,
                    stage="plan_queries",
                    source_type=query.source_type,
                    details={
                        "query_id": query.query_id,
                        "requested_window_seconds": window_seconds,
                        "maximum_window_seconds": capability.maximum_window_seconds,
                    },
                )

        # 5. Duplicate query detection
        param_canonical = self._canonical_params(query.parameters)
        if (query.source_type, param_canonical) in seen_queries:
            return StructuredError(
                code=DUPLICATE_QUERY,
                message=(
                    f"Duplicate query detected for source '{query.source_type}' with parameters {query.parameters}."
                ),
                retryable=False,
                stage="plan_queries",
                source_type=query.source_type,
                details={
                    "query_id": query.query_id,
                    "source_type": query.source_type.value if hasattr(query.source_type, "value") else str(query.source_type),
                    "parameters": query.parameters,
                },
            )

        return None

    @staticmethod
    def _extract_window_seconds(parameters: dict[str, Any]) -> float | None:
        if "window_seconds" in parameters:
            try:
                return float(parameters["window_seconds"])
            except (ValueError, TypeError):
                return None

        start_value = parameters.get("start_time", parameters.get("since"))
        end_value = parameters.get("end_time", parameters.get("until"))
        if start_value is not None and end_value is not None:
            try:
                st = start_value
                et = end_value
                start_dt = (
                    datetime.fromisoformat(st.replace("Z", "+00:00"))
                    if isinstance(st, str)
                    else st
                )
                end_dt = (
                    datetime.fromisoformat(et.replace("Z", "+00:00"))
                    if isinstance(et, str)
                    else et
                )
                return abs((end_dt - start_dt).total_seconds())
            except Exception:
                return None

        return None

    @staticmethod
    def _canonical_params(parameters: dict[str, Any]) -> str:
        """Create deterministic canonical string representation of query parameters."""
        try:
            return json.dumps(parameters, sort_keys=True, default=str)
        except Exception:
            return str(sorted(parameters.items()))

    @staticmethod
    def check_question_neutrality(question: str) -> tuple[bool, str | None]:
        """Check whether a query question uses neutral phrasing or assumes guilt.

        Returns (True, None) if neutral, or (False, reason) if leading.
        """
        leading_patterns = [
            "which deployment broke",
            "who broke",
            "faulty deployment",
            "bad deployment",
            "which bad commit",
            "faulty commit",
            "bad configuration introduced by",
            "culprit deployment",
            "why did deployment break",
        ]
        q_lower = question.lower()
        for p in leading_patterns:
            if p in q_lower:
                return False, f"Question contains leading / blame-assuming phrase '{p}'."
        return True, None
