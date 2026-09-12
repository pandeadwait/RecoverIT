"""
Investigation orchestration loop implementation.

Coordinates the bounded investigation workflow across missing-information analysis,
query planning, collection abstractions, context-building abstractions, hypothesis
generation and revision, citation validation, stopping rules, and deterministic ranking.

Boundary Rule: The orchestrator stops at producing a RankedHypothesisSet.
No remediation, actions, or self-healing paths exist.

See WORK_DIVISION.md §8.8, §8.9 and ARCHITECTURE.md §9.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    EvidenceType,
    InformationPriority,
    InvestigationState,
    InvestigationStatus,
    Reliability,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    BudgetUsage,
    Hypothesis,
    HypothesisSet,
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
    MissingInformationAssessment,
)
from investigation.budgets.budget_tracker import BudgetCost, BudgetTracker
from investigation.missing_information.assessor import MissingInformationAssessor
from investigation.orchestration.state_machine import (
    CheckpointStore,
    InvestigationStateMachine,
)
from investigation.query_planning.planner import EvidenceQueryPlanner
from reasoning.hypotheses.citation_validator import CitationValidator
from reasoning.hypotheses.generator import HypothesisGenerator
from reasoning.hypotheses.reviser import HypothesisReviser
from reasoning.provider.interface import ReasoningProvider
from reasoning.ranking.ranking_engine import RankingEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstraction Protocols (consumed from Person 1 and Person 2)
# ---------------------------------------------------------------------------


@runtime_checkable
class CollectionService(Protocol):
    """Protocol for Person 1 collection service."""

    async def execute(self, plan: EvidenceQueryPlan) -> RawEvidenceBatch:
        """Execute planned queries against external data sources."""
        ...


@runtime_checkable
class ContextBuilder(Protocol):
    """Protocol for Person 2 context builder."""

    async def build(
        self,
        incident_id: str,
        batch: RawEvidenceBatch,
        previous_context: IncidentContextSnapshot | None = None,
    ) -> IncidentContextSnapshot:
        """Construct or update an IncidentContextSnapshot from raw evidence."""
        ...


# ---------------------------------------------------------------------------
# In-Memory Stubs for Tests and Replays
# ---------------------------------------------------------------------------


class InMemoryCollectionService:
    """In-memory collection service stub for testing and deterministic replays."""

    def __init__(
        self,
        responses: dict[str, list[RawRecord]] | None = None,
        default_status: SourceStatus = SourceStatus.OK,
    ) -> None:
        self.responses = responses or {}
        self.default_status = default_status
        self.executed_plans: list[EvidenceQueryPlan] = []

    async def execute(self, plan: EvidenceQueryPlan) -> RawEvidenceBatch:
        self.executed_plans.append(plan)
        results: list[SourceResult] = []

        for q in plan.queries:
            st_val = q.source_type.value if hasattr(q.source_type, "value") else str(q.source_type)
            records = self.responses.get(
                q.query_id,
                [
                    RawRecord(
                        source_record_id=f"rec_{q.query_id}_1",
                        event_time=datetime.now(timezone.utc),
                        content_type="application/json",
                        payload={"message": f"Sample response for {st_val}"},
                    )
                ],
            )
            results.append(
                SourceResult(
                    query_id=q.query_id,
                    source_type=q.source_type,
                    source_adapter=f"{st_val}_adapter",
                    source_status=self.default_status,
                    records=records,
                )
            )

        return RawEvidenceBatch(
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            batch_id=f"batch_{len(self.executed_plans)}",
            collected_at=datetime.now(timezone.utc),
            results=results,
        )


class InMemoryContextBuilder:
    """In-memory context builder stub for testing and deterministic replays."""

    def __init__(
        self,
        synthetic_evidence: list[EvidenceSummaryProjection] | None = None,
    ) -> None:
        self.synthetic_evidence = synthetic_evidence or []
        self.built_snapshots: list[IncidentContextSnapshot] = []

    async def build(
        self,
        incident_id: str,
        batch: RawEvidenceBatch,
        previous_context: IncidentContextSnapshot | None = None,
    ) -> IncidentContextSnapshot:
        evidence = list(previous_context.evidence) if previous_context else []

        if self.synthetic_evidence:
            for item in self.synthetic_evidence:
                if not any(e.evidence_id == item.evidence_id for e in evidence):
                    evidence.append(item)
        else:
            for res in batch.results:
                st_val = res.source_type.value if hasattr(res.source_type, "value") else str(res.source_type)
                for rec in res.records:
                    ev_id = f"ev_{st_val}_{rec.source_record_id}"
                    if not any(e.evidence_id == ev_id for e in evidence):
                        evidence.append(
                            EvidenceSummaryProjection(
                                evidence_id=ev_id,
                                source_type=res.source_type,
                                evidence_type=(
                                    EvidenceType.ERROR_EVENT
                                    if st_val in {SourceType.LOGS.value, "logs"}
                                    else EvidenceType.METRIC_ANOMALY
                                ),
                                event_time=rec.event_time,
                                summary=rec.payload.get("message", "Evidence record"),
                                quality=EvidenceQuality(reliability=Reliability.HIGH),
                            )
                        )

        rev = (previous_context.revision + 1) if previous_context else 1
        snapshot = IncidentContextSnapshot(
            snapshot_id=f"ctx_{incident_id}_{rev}",
            incident_id=incident_id,
            revision=rev,
            created_at=datetime.now(timezone.utc),
            incident=(
                previous_context.incident
                if previous_context
                else IncidentSummary(
                    service="sample-service",
                    environment="production",
                    severity=Severity.CRITICAL,
                    detected_at=datetime.now(timezone.utc),
                    summary="Incident summary",
                )
            ),
            evidence=evidence,
            timeline=list(previous_context.timeline) if previous_context else [],
            relationships=list(previous_context.relationships) if previous_context else [],
            source_coverage=dict(previous_context.source_coverage) if previous_context else {},
            warnings=[],
        )
        self.built_snapshots.append(snapshot)
        return snapshot


# ---------------------------------------------------------------------------
# Stopping Rule Evaluator (WORK_DIVISION.md §8.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoppingDecision:
    """Outcome of stopping rule evaluation."""
    should_stop: bool
    is_inconclusive: bool = False
    stop_reason: StopReason | None = None
    reason: str = ""


class StoppingRuleEvaluator:
    """
    Evaluates whether the investigation loop should stop and rank,
    stop inconclusively, or continue another round.

    Rules implemented per WORK_DIVISION.md §8.9:
    1. Leading hypotheses have adequate evidence coverage -> stop & rank
    2. High-value questions resolved -> stop & rank
    3. Additional queries have low expected value -> stop & rank
    4. Maximum investigation budget reached -> stop & rank (or inconclusive if no evidence)
    5. Required sources unavailable -> stop inconclusive
    6. Insufficient hypotheses or zero valid evidence -> stop inconclusive
    """

    def __init__(
        self,
        min_supporting_sources_for_adequate: int = 2,
        min_evidence_score_for_confidence: float = 60.0,
    ) -> None:
        self.min_supporting_sources_for_adequate = min_supporting_sources_for_adequate
        self.min_evidence_score_for_confidence = min_evidence_score_for_confidence

    def evaluate(
        self,
        round_num: int,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        missing_info: MissingInformationAssessment | None = None,
        query_plan: EvidenceQueryPlan | None = None,
        budget_tracker: BudgetTracker | None = None,
        max_rounds: int = 3,
    ) -> StoppingDecision:
        """Evaluate all stopping rules in priority order."""
        # 1. Check budget exhaustion
        if budget_tracker is not None and budget_tracker.is_budget_exhausted():
            has_valid_evidence = self._has_any_valid_supporting_evidence(hypotheses, context)
            if has_valid_evidence:
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=False,
                    stop_reason=StopReason.BUDGET_EXHAUSTED,
                    reason="Budget exhausted with valid evidence; stopping to rank.",
                )
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=True,
                stop_reason=StopReason.BUDGET_EXHAUSTED,
                reason="Budget exhausted without valid supporting evidence.",
            )

        # 2. Check query plan stop reason
        if query_plan is not None and len(query_plan.queries) == 0:
            if query_plan.stop_reason == StopReason.SOURCES_UNAVAILABLE:
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=True,
                    stop_reason=StopReason.SOURCES_UNAVAILABLE,
                    reason="Required sources unavailable and further collection cannot help.",
                )
            if query_plan.stop_reason == StopReason.SUFFICIENT_EVIDENCE:
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=False,
                    stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                    reason="Evidence is sufficient; stopping to rank.",
                )
            if query_plan.stop_reason == StopReason.BUDGET_EXHAUSTED:
                has_valid = self._has_any_valid_supporting_evidence(hypotheses, context)
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=not has_valid,
                    stop_reason=StopReason.BUDGET_EXHAUSTED,
                    reason="Query planning halted due to budget exhaustion.",
                )

        # 3. Check hypothesis sufficiency
        if hypotheses is not None and len(hypotheses.hypotheses) == 0:
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=True,
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                reason="Fewer than the minimum hypotheses generated.",
            )

        # 4. Check if leading hypotheses have adequate evidence coverage
        if hypotheses is not None and len(hypotheses.hypotheses) > 0:
            context_ids = {e.evidence_id for e in context.evidence}
            for h in hypotheses.hypotheses:
                valid_citations = [c for c in h.supporting_evidence if c.evidence_id in context_ids]
                sources = {
                    e.source_type
                    for e in context.evidence
                    if any(c.evidence_id == e.evidence_id for c in valid_citations)
                }
                if len(sources) >= self.min_supporting_sources_for_adequate:
                    return StoppingDecision(
                        should_stop=True,
                        is_inconclusive=False,
                        stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                        reason=f"Leading hypothesis '{h.hypothesis_id}' has adequate coverage ({len(sources)} sources).",
                    )

        # 5. Check if high-value questions are resolved
        if missing_info is not None:
            high_priority_gaps = [
                item for item in missing_info.missing_information
                if item.priority == InformationPriority.HIGH
            ]
            if not high_priority_gaps and len(missing_info.known_facts) >= 2:
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=False,
                    stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                    reason="High-value questions resolved and facts established.",
                )

        # 6. Check round limit
        if round_num >= max_rounds:
            has_valid = self._has_any_valid_supporting_evidence(hypotheses, context)
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=not has_valid,
                stop_reason=(
                    StopReason.SUFFICIENT_EVIDENCE
                    if has_valid
                    else StopReason.INSUFFICIENT_EVIDENCE
                ),
                reason=f"Reached maximum round limit ({max_rounds}).",
            )

        # Default: continue investigation
        return StoppingDecision(should_stop=False, reason="Additional evidence useful.")

    @staticmethod
    def _has_any_valid_supporting_evidence(
        hypotheses: HypothesisSet | None,
        context: IncidentContextSnapshot,
    ) -> bool:
        if not hypotheses or not hypotheses.hypotheses or not context.evidence:
            return False
        context_ids = {e.evidence_id for e in context.evidence}
        return any(
            any(c.evidence_id in context_ids for c in h.supporting_evidence)
            for h in hypotheses.hypotheses
        )


# ---------------------------------------------------------------------------
# Investigation Orchestrator
# ---------------------------------------------------------------------------


class InvestigationOrchestrator:
    """
    Bounded investigation orchestrator driving Person 3's end-to-end loop.

    Guarantees:
    - Driven by the InvestigationStateMachine with strict legal transitions.
    - Uses CollectionService and ContextBuilder protocols (never calls implementations directly).
    - Enforces InvestigationBudget via BudgetTracker.
    - Checkpoints state before and after each external call.
    - Respects all stopping rules (adequate coverage, budget, missing sources).
    - Boundary enforcement: Stops at RankedHypothesisSet; no remediation exists.
    """

    def __init__(
        self,
        provider: ReasoningProvider,
        collection_service: CollectionService,
        context_builder: ContextBuilder,
        budget_tracker: BudgetTracker | None = None,
        assessor: MissingInformationAssessor | None = None,
        planner: EvidenceQueryPlanner | None = None,
        generator: HypothesisGenerator | None = None,
        reviser: HypothesisReviser | None = None,
        citation_validator: CitationValidator | None = None,
        ranking_engine: RankingEngine | None = None,
        checkpoint_store: CheckpointStore | None = None,
        stopping_evaluator: StoppingRuleEvaluator | None = None,
    ) -> None:
        self._provider = provider
        self._collection_service = collection_service
        self._context_builder = context_builder
        self._budget_tracker = budget_tracker
        self._citation_validator = citation_validator or CitationValidator()
        self._assessor = assessor or MissingInformationAssessor(provider=provider)
        self._planner = planner or EvidenceQueryPlanner(
            provider=provider, budget_tracker=budget_tracker
        )
        self._generator = generator or HypothesisGenerator(
            provider=provider, citation_validator=self._citation_validator
        )
        self._reviser = reviser or HypothesisReviser(
            provider=provider, citation_validator=self._citation_validator
        )
        self._ranking_engine = ranking_engine or RankingEngine()
        self._checkpoint_store = checkpoint_store
        self._stopping_evaluator = stopping_evaluator or StoppingRuleEvaluator()

        # State tracking
        self._state_machine: InvestigationStateMachine | None = None
        self._current_context: IncidentContextSnapshot | None = None
        self._current_hypotheses: HypothesisSet | None = None
        self._history_query_plans: list[EvidenceQueryPlan] = []
        self._history_batches: list[RawEvidenceBatch] = []

    @property
    def state_machine(self) -> InvestigationStateMachine | None:
        """The active state machine."""
        return self._state_machine

    @property
    def budget_tracker(self) -> BudgetTracker | None:
        """The active budget tracker."""
        return self._budget_tracker

    @property
    def current_context(self) -> IncidentContextSnapshot | None:
        """The latest context snapshot."""
        return self._current_context

    @property
    def current_hypotheses(self) -> HypothesisSet | None:
        """The latest hypothesis set."""
        return self._current_hypotheses

    @property
    def history_query_plans(self) -> list[EvidenceQueryPlan]:
        """All query plans generated across rounds."""
        return list(self._history_query_plans)

    @property
    def history_batches(self) -> list[RawEvidenceBatch]:
        """All raw evidence batches collected across rounds."""
        return list(self._history_batches)

    async def run(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        budget: InvestigationBudget | None = None,
        initial_context: IncidentContextSnapshot | None = None,
    ) -> RankedHypothesisSet:
        """
        Execute the bounded investigation loop end-to-end.
        """
        # 1. Setup budget tracker
        effective_budget = budget or InvestigationBudget()
        if self._budget_tracker is None:
            self._budget_tracker = BudgetTracker(budget=effective_budget)

        # 2. Setup state machine
        self._state_machine = InvestigationStateMachine(
            incident_id=incident.incident_id,
            store=self._checkpoint_store,
        )

        # 3. Setup initial context
        self._current_context = (
            initial_context
            if initial_context is not None
            else self._create_empty_context(incident, source_capabilities)
        )
        self._current_hypotheses = None
        self._history_query_plans = []
        self._history_batches = []

        round_num = 1
        max_rounds = effective_budget.max_rounds

        # Transition: RECEIVED -> ASSESSING_GAPS
        self._state_machine.transition_to(
            InvestigationState.ASSESSING_GAPS,
            reason="Starting initial gap assessment.",
        )

        while not self._state_machine.is_terminal:
            self._budget_tracker.record_round()

            # --- Step A: Assess Missing Information ---
            active_list = (
                [h for h in self._current_hypotheses.hypotheses]
                if self._current_hypotheses
                else None
            )

            self._state_machine.checkpoint_before_call(
                "MissingInformationAssessor.assess",
                payload={"round": round_num},
            )
            missing_info = await self._assessor.assess(
                incident=incident,
                source_capabilities=source_capabilities,
                context=self._current_context,
                active_hypotheses=active_list,
            )
            self._budget_tracker.record_reasoning_call()
            self._state_machine.checkpoint_after_call(
                "MissingInformationAssessor.assess",
                payload={"round": round_num},
            )

            # Collect all historical queries to avoid duplicates
            all_historical_queries: list[EvidenceQueryPlanQuery] = [
                q for plan in self._history_query_plans for q in plan.queries
            ]

            # --- Step B: Plan Allowed Queries ---
            query_plan = await self._planner.plan(
                missing_information=missing_info,
                source_capabilities=source_capabilities,
                context=self._current_context,
                budget=effective_budget,
                round_num=round_num,
                history_queries=all_historical_queries,
            )
            self._history_query_plans.append(query_plan)

            # Handle zero queries in plan
            if len(query_plan.queries) == 0:
                return self._handle_zero_queries(
                    query_plan=query_plan,
                    incident=incident,
                )

            # --- Step C: Collect Evidence (Person 1) ---
            self._state_machine.transition_to(
                InvestigationState.COLLECTING_EVIDENCE,
                reason=f"Executing {len(query_plan.queries)} planned queries in round {round_num}.",
            )

            self._state_machine.checkpoint_before_call(
                "CollectionService.execute",
                payload={"plan_id": query_plan.plan_id},
            )
            raw_batch = await self._collection_service.execute(query_plan)
            self._budget_tracker.record_queries(len(query_plan.queries))
            self._history_batches.append(raw_batch)
            self._state_machine.checkpoint_after_call(
                "CollectionService.execute",
                payload={"batch_id": raw_batch.batch_id},
            )

            # Check for catastrophic source unavailability
            all_unavailable = (
                len(raw_batch.results) > 0
                and all(
                    r.source_status in {SourceStatus.UNAVAILABLE, SourceStatus.ERROR}
                    for r in raw_batch.results
                )
            )
            if all_unavailable and not self._current_context.evidence:
                self._state_machine.transition_to(
                    InvestigationState.INCONCLUSIVE,
                    reason="Required sources unavailable and no prior evidence collected.",
                )
                return self._ranking_engine.rank(
                    hypothesis_set=HypothesisSet(
                        incident_id=incident.incident_id,
                        hypotheses=[],
                        generated_at=datetime.now(timezone.utc),
                    ),
                    context=self._current_context,
                    budget_usage=self._budget_tracker.get_budget_usage(),
                    status=InvestigationStatus.INCONCLUSIVE,
                    stop_reason=StopReason.SOURCES_UNAVAILABLE,
                    remaining_uncertainty=["Required operational data sources are unavailable."],
                )

            # --- Step D: Build Timeline & Context (Person 2) ---
            self._state_machine.transition_to(
                InvestigationState.BUILDING_TIMELINE,
                reason=f"Building context from raw batch {raw_batch.batch_id}.",
            )

            self._state_machine.checkpoint_before_call(
                "ContextBuilder.build",
                payload={"batch_id": raw_batch.batch_id},
            )
            self._current_context = await self._context_builder.build(
                incident_id=incident.incident_id,
                batch=raw_batch,
                previous_context=self._current_context,
            )
            self._state_machine.checkpoint_after_call(
                "ContextBuilder.build",
                payload={"snapshot_id": self._current_context.snapshot_id},
            )

            # --- Step E: Generate or Revise Hypotheses (Person 3) ---
            self._state_machine.transition_to(
                InvestigationState.GENERATING_HYPOTHESES,
                reason=(
                    f"Generating initial hypotheses in round {round_num}."
                    if self._current_hypotheses is None
                    else f"Revising hypotheses in round {round_num}."
                ),
            )

            if self._current_hypotheses is None:
                self._state_machine.checkpoint_before_call(
                    "HypothesisGenerator.generate",
                    payload={"round": round_num},
                )
                self._current_hypotheses = await self._generator.generate(
                    incident=incident,
                    context=self._current_context,
                    limits=effective_budget,
                )
                self._budget_tracker.record_reasoning_call()
                self._state_machine.checkpoint_after_call(
                    "HypothesisGenerator.generate",
                    payload={"count": len(self._current_hypotheses.hypotheses)},
                )
            else:
                self._state_machine.checkpoint_before_call(
                    "HypothesisReviser.revise",
                    payload={"round": round_num},
                )
                self._current_hypotheses = await self._reviser.revise(
                    previous_hypotheses=self._current_hypotheses,
                    new_context=self._current_context,
                )
                self._budget_tracker.record_reasoning_call()
                self._state_machine.checkpoint_after_call(
                    "HypothesisReviser.revise",
                    payload={"count": len(self._current_hypotheses.hypotheses)},
                )

            # --- Step F: Check Stopping Rules ---
            decision = self._stopping_evaluator.evaluate(
                round_num=round_num,
                context=self._current_context,
                hypotheses=self._current_hypotheses,
                missing_info=missing_info,
                query_plan=query_plan,
                budget_tracker=self._budget_tracker,
                max_rounds=max_rounds,
            )

            if decision.should_stop:
                if decision.is_inconclusive:
                    self._state_machine.transition_to(
                        InvestigationState.INCONCLUSIVE,
                        reason=decision.reason,
                    )
                    return self._ranking_engine.rank(
                        hypothesis_set=self._current_hypotheses,
                        context=self._current_context,
                        budget_usage=self._budget_tracker.get_budget_usage(),
                        status=InvestigationStatus.INCONCLUSIVE,
                        stop_reason=decision.stop_reason or StopReason.INSUFFICIENT_EVIDENCE,
                        remaining_uncertainty=[decision.reason],
                    )
                else:
                    self._state_machine.transition_to(
                        InvestigationState.RANKING,
                        reason=decision.reason,
                    )
                    ranked_set = self._ranking_engine.rank(
                        hypothesis_set=self._current_hypotheses,
                        context=self._current_context,
                        budget_usage=self._budget_tracker.get_budget_usage(),
                    )
                    self._state_machine.transition_to(
                        InvestigationState.COMPLETED,
                        reason="Final hypothesis ranking completed.",
                    )
                    return ranked_set

            # Loop back to ASSESSING_GAPS
            self._state_machine.transition_to(
                InvestigationState.ASSESSING_GAPS,
                reason=f"Continuing to round {round_num + 1} for further evidence.",
            )
            round_num += 1

        # Fallback if loop exits without explicit return
        return self._finish_inconclusive(
            incident_id=incident.incident_id,
            reason="Investigation stopped without explicit conclusion.",
            stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
        )

    def _handle_zero_queries(
        self,
        query_plan: EvidenceQueryPlan,
        incident: IncidentSeed,
    ) -> RankedHypothesisSet:
        """Handle cases where the query planner produces zero queries."""
        assert self._state_machine is not None
        assert self._budget_tracker is not None
        assert self._current_context is not None

        has_valid_support = (
            self._current_hypotheses is not None
            and any(
                any(
                    c.evidence_id in {e.evidence_id for e in self._current_context.evidence}
                    for c in h.supporting_evidence
                )
                for h in self._current_hypotheses.hypotheses
            )
        )

        if query_plan.stop_reason == StopReason.SOURCES_UNAVAILABLE:
            self._state_machine.transition_to(
                InvestigationState.INCONCLUSIVE,
                reason="Required sources unavailable.",
            )
            return self._ranking_engine.rank(
                hypothesis_set=self._current_hypotheses or HypothesisSet(
                    incident_id=incident.incident_id,
                    hypotheses=[],
                    generated_at=datetime.now(timezone.utc),
                ),
                context=self._current_context,
                budget_usage=self._budget_tracker.get_budget_usage(),
                status=InvestigationStatus.INCONCLUSIVE,
                stop_reason=StopReason.SOURCES_UNAVAILABLE,
                remaining_uncertainty=["Required operational data sources are unavailable."],
            )

        if has_valid_support and self._current_hypotheses is not None:
            # We already have hypotheses with valid evidence -> proceed to ranking!
            # Note: From ASSESSING_GAPS, state machine allows INCONCLUSIVE or COLLECTING_EVIDENCE.
            # To rank, we need to be in GENERATING_HYPOTHESES -> RANKING -> COMPLETED.
            # But if we are in ASSESSING_GAPS without queries:
            # We can stop inconclusive or complete via RANKING if allowed.
            # However, from ASSESSING_GAPS legal transitions are COLLECTING_EVIDENCE, INCONCLUSIVE, CANCELLED.
            # Therefore, if zero queries at ASSESSING_GAPS, it is inconclusive (e.g. budget exhausted):
            self._state_machine.transition_to(
                InvestigationState.INCONCLUSIVE,
                reason="Zero queries planned; investigation halted.",
            )
            return self._ranking_engine.rank(
                hypothesis_set=self._current_hypotheses,
                context=self._current_context,
                budget_usage=self._budget_tracker.get_budget_usage(),
                status=InvestigationStatus.INCONCLUSIVE,
                stop_reason=query_plan.stop_reason or StopReason.BUDGET_EXHAUSTED,
                remaining_uncertainty=["Zero additional queries could be planned."],
            )

        self._state_machine.transition_to(
            InvestigationState.INCONCLUSIVE,
            reason="No queries could be planned and no valid evidence exists.",
        )
        return self._ranking_engine.rank(
            hypothesis_set=self._current_hypotheses or HypothesisSet(
                incident_id=incident.incident_id,
                hypotheses=[],
                generated_at=datetime.now(timezone.utc),
            ),
            context=self._current_context,
            budget_usage=self._budget_tracker.get_budget_usage(),
            status=InvestigationStatus.INCONCLUSIVE,
            stop_reason=query_plan.stop_reason or StopReason.INSUFFICIENT_EVIDENCE,
            remaining_uncertainty=["Zero queries could be planned; evidence is insufficient."],
        )

    def _finish_inconclusive(
        self,
        incident_id: str,
        reason: str,
        stop_reason: StopReason,
    ) -> RankedHypothesisSet:
        assert self._state_machine is not None
        assert self._budget_tracker is not None
        assert self._current_context is not None

        if not self._state_machine.is_terminal:
            self._state_machine.transition_to(
                InvestigationState.INCONCLUSIVE,
                reason=reason,
            )

        return self._ranking_engine.rank(
            hypothesis_set=self._current_hypotheses or HypothesisSet(
                incident_id=incident_id,
                hypotheses=[],
                generated_at=datetime.now(timezone.utc),
            ),
            context=self._current_context,
            budget_usage=self._budget_tracker.get_budget_usage(),
            status=InvestigationStatus.INCONCLUSIVE,
            stop_reason=stop_reason,
            remaining_uncertainty=[reason],
        )

    @staticmethod
    def _create_empty_context(
        incident: IncidentSeed,
        catalog: SourceCapabilityCatalog,
    ) -> IncidentContextSnapshot:
        """Construct the initial empty context snapshot."""
        return IncidentContextSnapshot(
            snapshot_id=f"ctx_{incident.incident_id}_0",
            incident_id=incident.incident_id,
            revision=0,
            created_at=incident.received_at,
            incident=IncidentSummary(
                service=incident.service,
                environment=incident.environment,
                severity=incident.severity,
                detected_at=incident.detected_at,
                summary=incident.summary,
            ),
            evidence=[],
            timeline=[],
            relationships=[],
            source_coverage={
                (src.source_type.value if hasattr(src.source_type, "value") else str(src.source_type)): SourceCoverageStatus.NOT_QUERIED
                for src in catalog.sources
            },
            warnings=[],
        )
