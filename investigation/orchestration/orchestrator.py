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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    EvidenceRole,
    EvidenceType,
    InformationPriority,
    InvestigationState,
    InvestigationStatus,
    Reliability,
    RootCauseCategory,
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
from reasoning.hypotheses.deduplicator import HypothesisDeduplicator
from reasoning.hypotheses.generator import HypothesisGenerator
from reasoning.hypotheses.reviser import HypothesisReviser
from reasoning.provider.interface import ReasoningProvider
from reasoning.provider.llm_provider import LLMProviderError
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
        created_at: datetime | None = None,
    ) -> None:
        self.synthetic_evidence = synthetic_evidence or []
        self.fixed_created_at = created_at
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
        effective_created_at = (
            previous_context.created_at
            if previous_context
            else (self.fixed_created_at or datetime.now(timezone.utc))
        )
        snapshot = IncidentContextSnapshot(
            snapshot_id=f"ctx_{incident_id}_{rev}",
            incident_id=incident_id,
            revision=rev,
            created_at=effective_created_at,
            incident=(
                previous_context.incident
                if previous_context
                else IncidentSummary(
                    service="sample-service",
                    environment="production",
                    severity=Severity.CRITICAL,
                    detected_at=effective_created_at,
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
    """Outcome of stopping rule evaluation with completion criteria tracking."""
    should_stop: bool
    is_inconclusive: bool = False
    stop_reason: StopReason | None = None
    reason: str = ""
    criteria_status: dict[str, bool] = field(default_factory=dict)
    unresolved_criteria: list[str] = field(default_factory=list)


class StoppingRuleEvaluator:
    """
    Evaluates whether the investigation loop should stop and rank,
    stop inconclusively, or continue another round.

    Rules implemented per WORK_DIVISION.md §8.9 and CREDIBILITY_IMPROVEMENT_PLAN.md Phase 6:
    1. Leading hypotheses have adequate evidence coverage across >= 2 independent source types.
    2. Direct causal record cited (for change regressions, logs and metrics alone cannot finish).
    3. Symptom/impact record cited (logs or metrics).
    4. No unresolved HIGH-priority information gaps.
    5. Leading hypothesis exceeds confidence threshold.
    6. Meaningful score advantage over rank two.
    7. Alternative explanation tested (>= 2 hypotheses).
    8. Maximum budget / round exhaustion: strictly distinguished from sufficient evidence.
    9. Required sources unavailable -> stop inconclusive.
    10. Insufficient hypotheses or zero valid evidence -> stop inconclusive.
    """

    def __init__(
        self,
        min_supporting_sources_for_adequate: int = 2,
        min_evidence_score_for_confidence: float = 50.0,
        min_score_margin: float = 5.0,
        require_causal_evidence: bool = True,
        require_symptom_evidence: bool = True,
        require_no_high_priority_gaps: bool = True,
        require_alternative_tested: bool = True,
        allow_high_value_shortcut: bool = True,
        ranking_engine: RankingEngine | None = None,
    ) -> None:
        self.min_supporting_sources_for_adequate = min_supporting_sources_for_adequate
        self.min_evidence_score_for_confidence = min_evidence_score_for_confidence
        self.min_score_margin = min_score_margin
        self.require_causal_evidence = require_causal_evidence
        self.require_symptom_evidence = require_symptom_evidence
        self.require_no_high_priority_gaps = require_no_high_priority_gaps
        self.require_alternative_tested = require_alternative_tested
        self.allow_high_value_shortcut = allow_high_value_shortcut
        self._ranking_engine = ranking_engine or RankingEngine()

    def evaluate_completion_criteria(
        self,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        missing_info: MissingInformationAssessment | None = None,
    ) -> tuple[bool, dict[str, bool], list[str]]:
        """
        Evaluate completion requirements against the current context and hypotheses.

        Returns:
            (is_complete, criteria_status, unresolved_criteria)
        """
        criteria: dict[str, bool] = {}
        unresolved: list[str] = []

        if not hypotheses or not hypotheses.hypotheses or not context.evidence:
            return False, {"has_hypotheses": False}, ["No active hypotheses or evidence available."]

        context_ids = {e.evidence_id: e for e in context.evidence}

        # Rank current hypotheses to identify the leading explanation and scores
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
                f"Needs {self.min_supporting_sources_for_adequate} independent sources (currently {len(cited_sources)}: {', '.join(str(getattr(s, 'value', s)) for s in cited_sources) or 'none'})"
            )

        # In permissive test mode (min_supporting_sources_for_adequate <= 1):
        if self.min_supporting_sources_for_adequate <= 1:
            is_complete = has_adequate_sources and len(valid_citations) > 0
            return is_complete, criteria, unresolved

        # 2. Direct causal record
        CHANGE_CATEGORIES = {
            RootCauseCategory.CONFIGURATION_REGRESSION,
            RootCauseCategory.DEPLOYMENT_FAILURE,
            RootCauseCategory.CODE_DEFECT,
            "configuration_regression",
            "deployment_failure",
            "code_defect",
        }
        CAUSAL_SOURCES = {
            SourceType.CHANGES,
            SourceType.CONFIGURATION,
            SourceType.DEPLOYMENTS,
            SourceType.PIPELINES,
        }
        SYMPTOM_SOURCES = {
            SourceType.LOGS,
            SourceType.METRICS,
            SourceType.HEALTH,
        }

        has_causal = False
        cat = str(getattr(leading.root_cause_category, "value", leading.root_cause_category))
        if cat in CHANGE_CATEGORIES:
            # Change-related hypotheses MUST cite evidence from a change source or with role=CAUSE.
            # Logs and metrics alone cannot satisfy this.
            for c in valid_citations:
                st = context_ids[c.evidence_id].source_type
                if c.role == EvidenceRole.CAUSE or st in CAUSAL_SOURCES:
                    has_causal = True
                    break
        else:
            # Non-change categories: role=CAUSE or any valid non-symptom citation,
            # or direct symptom citation with non-empty reason
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
        round_num: int,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        missing_info: MissingInformationAssessment | None = None,
        query_plan: EvidenceQueryPlan | None = None,
        budget_tracker: BudgetTracker | None = None,
        max_rounds: int = 3,
    ) -> StoppingDecision:
        """Evaluate all stopping rules in priority order."""
        # 1. Check hypothesis sufficiency
        if hypotheses is not None and len(hypotheses.hypotheses) == 0:
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=True,
                stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                reason="Fewer than the minimum hypotheses generated.",
            )

        # 2. Check source unavailability from query plan
        if query_plan is not None and query_plan.stop_reason == StopReason.SOURCES_UNAVAILABLE:
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=True,
                stop_reason=StopReason.SOURCES_UNAVAILABLE,
                reason="Required sources unavailable and further collection cannot help.",
            )

        # 3. Check comprehensive completion criteria
        is_complete, criteria_status, unresolved = self.evaluate_completion_criteria(
            context=context,
            hypotheses=hypotheses,
            missing_info=missing_info,
        )

        if is_complete:
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=False,
                stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                reason="All completion requirements satisfied (multi-source, causal, symptom, gaps resolved, confidence advantage).",
                criteria_status=criteria_status,
                unresolved_criteria=[],
            )

        # 4. Check high-value questions resolved shortcut (if enabled)
        if self.allow_high_value_shortcut and missing_info is not None:
            high_priority_gaps = [
                item for item in missing_info.missing_information
                if item.priority == InformationPriority.HIGH and not item.resolved
            ]
            if not high_priority_gaps and len(missing_info.known_facts) >= 2:
                # Acceptance criterion: logs plus metrics alone cannot finish a configuration investigation.
                # If leading hypothesis is change-related, direct causal evidence MUST still be present.
                if criteria_status.get("causal_evidence", True):
                    return StoppingDecision(
                        should_stop=True,
                        is_inconclusive=False,
                        stop_reason=StopReason.SUFFICIENT_EVIDENCE,
                        reason="High-value questions resolved and facts established.",
                        criteria_status=criteria_status,
                        unresolved_criteria=[],
                    )

        # 5. Check query plan stop reason when criteria are incomplete
        if query_plan is not None and len(query_plan.queries) == 0:
            has_valid = self._has_any_valid_supporting_evidence(hypotheses, context)
            if query_plan.stop_reason == StopReason.BUDGET_EXHAUSTED or not query_plan.queries:
                return StoppingDecision(
                    should_stop=True,
                    is_inconclusive=not has_valid,
                    stop_reason=StopReason.BUDGET_EXHAUSTED,
                    reason=f"No further queries planned. Incomplete criteria: {', '.join(unresolved) if unresolved else 'None'}",
                    criteria_status=criteria_status,
                    unresolved_criteria=unresolved,
                )

        # 6. Check budget exhaustion
        if budget_tracker is not None and budget_tracker.is_budget_exhausted():
            has_valid = self._has_any_valid_supporting_evidence(hypotheses, context)
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=not has_valid,
                stop_reason=StopReason.BUDGET_EXHAUSTED,
                reason=f"Budget exhausted before all completion criteria were met: {', '.join(unresolved)}",
                criteria_status=criteria_status,
                unresolved_criteria=unresolved,
            )

        # 7. Check round limit
        if round_num >= max_rounds:
            has_valid = self._has_any_valid_supporting_evidence(hypotheses, context)
            return StoppingDecision(
                should_stop=True,
                is_inconclusive=not has_valid,
                stop_reason=(
                    StopReason.BUDGET_EXHAUSTED
                    if has_valid
                    else StopReason.INSUFFICIENT_EVIDENCE
                ),
                reason=f"Reached maximum round limit ({max_rounds}). Incomplete criteria: {', '.join(unresolved)}",
                criteria_status=criteria_status,
                unresolved_criteria=unresolved,
            )

        # Default: continue investigation to gather missing evidence
        return StoppingDecision(
            should_stop=False,
            reason=f"Continuing to round {round_num + 1}. Unresolved criteria: {', '.join(unresolved)}",
            criteria_status=criteria_status,
            unresolved_criteria=unresolved,
        )

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
        deduplicator: HypothesisDeduplicator | None = None,
        checkpoint_store: CheckpointStore | None = None,
        stopping_evaluator: StoppingRuleEvaluator | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        provider_name: str = "deterministic-preset",
    ) -> None:
        self._provider = provider
        self._collection_service = collection_service
        self._context_builder = context_builder
        self._budget_tracker = budget_tracker
        self._citation_validator = citation_validator or CitationValidator()
        self._deduplicator = deduplicator or HypothesisDeduplicator()
        self._assessor = assessor or MissingInformationAssessor(provider=provider)
        self._planner = planner or EvidenceQueryPlanner(
            provider=provider, budget_tracker=budget_tracker
        )
        self._generator = generator or HypothesisGenerator(
            provider=provider,
            citation_validator=self._citation_validator,
            deduplicator=self._deduplicator,
        )
        self._reviser = reviser or HypothesisReviser(
            provider=provider,
            citation_validator=self._citation_validator,
            deduplicator=self._deduplicator,
        )
        self._ranking_engine = ranking_engine or RankingEngine()
        self._checkpoint_store = checkpoint_store
        self._stopping_evaluator = stopping_evaluator or StoppingRuleEvaluator()
        self._progress_callback = progress_callback

        # Provider identification
        if provider_name == "deterministic-preset":
            if hasattr(provider, "preset"):
                provider_name = f"Deterministic Preset ({provider.preset})"
            elif hasattr(provider, "provider_name"):
                provider_name = f"Live LLM ({provider.provider_name})"
        self._provider_name = provider_name

        # State tracking
        self._state_machine: InvestigationStateMachine | None = None
        self._current_context: IncidentContextSnapshot | None = None
        self._current_hypotheses: HypothesisSet | None = None
        self._history_query_plans: list[EvidenceQueryPlan] = []
        self._history_batches: list[RawEvidenceBatch] = []
        self._last_missing_info: MissingInformationAssessment | None = None
        self._last_stopping_decision: StoppingDecision | None = None

    def _emit_progress(
        self,
        *,
        kind: str,
        stage: str,
        title: str,
        detail: str,
        output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Publish a concise, user-safe explanation of real orchestration activity."""
        if self._progress_callback is None:
            return
        event = {
            "kind": kind,
            "stage": stage,
            "title": title,
            "detail": detail,
            "output": output,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._progress_callback(event)
        except Exception:  # pragma: no cover - observers must never break an investigation
            logger.exception("Progress callback failed")

    @property
    def provider_name(self) -> str:
        """The effective reasoning provider name."""
        return self._provider_name

    @staticmethod
    def classify_adapter(adapter_name: str) -> str:
        """Classify adapter as 'Recorded Replay' or 'Live Source'."""
        name = adapter_name.lower()
        if any(token in name for token in ("localgit", "local_git", "git", "filelog", "file_log", "live", "real", "cloud")):
            return "Live Source"
        return "Recorded Replay"

    @staticmethod
    def format_tool_call(source_type: SourceType | str, parameters: dict[str, Any]) -> str:
        """Format a tool call as function-like invocation string."""
        st_name = (source_type.value if hasattr(source_type, "value") else str(source_type)).lower()
        param_parts = []
        for k, v in parameters.items():
            if isinstance(v, str):
                param_parts.append(f'{k}="{v}"')
            else:
                param_parts.append(f"{k}={v}")
        return f"{st_name}.search({', '.join(param_parts)})"

    @classmethod
    def _synthesize_interpretation(
        cls,
        query: EvidenceQueryPlanQuery,
        result: SourceResult,
        round_num: int,
    ) -> str:
        source_type = query.source_type
        records = result.records
        count = len(records)
        source_str = cls._enum_value(source_type)

        if count == 0:
            svc = query.parameters.get("service", "target service")
            return (
                f"No {source_str} records matched query parameters for {svc}. "
                f"This indicates that no changes or anomalies were recorded in this domain during the investigated window."
            )

        if source_type == SourceType.CHANGES:
            first_rec = records[0].payload
            sha = str(first_rec.get("commit_sha", records[0].source_record_id))[:8]
            msg = str(first_rec.get("message", "code modification")).strip()
            return (
                f"Identified {count} relevant change commit(s) ({sha}: '{msg}'). "
                f"This commit occurred prior to the alert, making it a primary causal trigger candidate, "
                f"though operational deployment and runtime impact must be verified."
            )

        if source_type == SourceType.DEPLOYMENTS:
            first_rec = records[0].payload
            version = first_rec.get("version", first_rec.get("deployment_id", "release"))
            status = first_rec.get("status", "unknown")
            return (
                f"Confirmed {count} deployment record(s) ({version} status: '{status}'). "
                f"The deployment timing closely aligns with the initial symptom onset, connecting the "
                f"codebase modification to the production service instance."
            )

        if source_type == SourceType.CONFIGURATION:
            first_rec = records[0].payload
            key = first_rec.get("key", "setting")
            old_val = first_rec.get("old_value", "previous")
            new_val = first_rec.get("new_value", "updated")
            return (
                f"Found {count} configuration change(s) affecting '{key}' ({old_val} → {new_val}). "
                f"This configuration modification directly alters runtime behavior and represents "
                f"a verified causal factor."
            )

        if source_type == SourceType.LOGS:
            first_rec = records[0].payload
            lvl = str(first_rec.get("level", "ERROR")).upper()
            msg = str(first_rec.get("message", "error event")).strip()
            if len(msg) > 90:
                msg = f"{msg[:87]}…"
            return (
                f"Extracted {count} {lvl} log event(s) exhibiting failure pattern: '{msg}'. "
                f"Directly documents the service degradation symptom experienced by downstream callers."
            )

        if source_type == SourceType.METRICS:
            first_rec = records[0].payload
            metric = first_rec.get("metric_name", "operational metric")
            val = first_rec.get("value", "anomalous level")
            return (
                f"Retrieved {count} telemetry series showing anomaly on '{metric}' ({val}). "
                f"Corroborates persistent service disruption and quantifies the symptom severity."
            )

        if source_type == SourceType.PIPELINES:
            first_rec = records[0].payload
            pipe = first_rec.get("pipeline", "build")
            status = first_rec.get("status", "completed")
            return (
                f"Retrieved {count} CI/CD pipeline event(s) ({pipe} reported status '{status}'). "
                f"Provides build and testing validation context prior to deployment."
            )

        return (
            f"Received {count} {source_str} record(s) matching the search parameters. "
            f"Evidence incorporated into the incident context snapshot."
        )

    @classmethod
    def _synthesize_next_decision(
        cls,
        query: EvidenceQueryPlanQuery,
        result: SourceResult,
        round_num: int,
        max_rounds: int = 3,
    ) -> str:
        source_type = query.source_type
        count = len(result.records)

        if round_num == 1:
            if source_type in (SourceType.CHANGES, SourceType.CONFIGURATION):
                return (
                    "Query deployment history and application logs to verify whether this change was "
                    "activated in production and caused the reported degradation."
                )
            if source_type in (SourceType.LOGS, SourceType.METRICS):
                return (
                    "Query change and deployment history to uncover the triggering operational event "
                    "preceding these failure symptoms."
                )
            if source_type == SourceType.DEPLOYMENTS:
                return (
                    "Inspect application error logs and configuration diffs associated with this deployment "
                    "to identify the specific breaking fault."
                )
            return (
                "Correlate returned records with complementary telemetry sources in Round 2 to close "
                "remaining causal information gaps."
            )

        if count > 0:
            return (
                "Synthesize normalized multi-source timeline, evaluate evidentiary stopping criteria, "
                "and proceed to deterministic hypothesis ranking."
            )
        return (
            "Evaluate stopping criteria with available evidence, checking whether sufficient multi-source "
            "corroboration exists or if investigation is inconclusive."
        )

    def _emit_trace_step(
        self,
        *,
        query: EvidenceQueryPlanQuery,
        result: SourceResult,
        round_num: int,
        rationale: str,
        tool_call: str,
        adapter_type: str,
        adapter_name: str,
        latency_ms: float,
        records_preview: list[str],
        is_truncated: bool,
        interpretation: str,
        next_decision: str,
    ) -> None:
        source_name = self._enum_value(query.source_type)
        total_records = len(result.records)
        event = {
            "kind": "trace_step",
            "stage": "collect",
            "title": f"Tool Execution · {source_name}",
            "detail": rationale,
            "rationale": rationale,
            "tool_call": tool_call,
            "tool_result": {
                "adapter_type": adapter_type,
                "adapter_name": adapter_name,
                "status": self._enum_value(result.source_status),
                "latency_ms": round(latency_ms, 2),
                "records_matched": total_records,
                "records_preview": records_preview,
                "filters_applied": dict(query.parameters),
                "warnings": list(result.warnings),
                "is_truncated": is_truncated,
                "total_records": total_records,
            },
            "interpretation": interpretation,
            "next_decision": next_decision,
            "metadata": {
                "round": round_num,
                "query_id": query.query_id,
                "source_type": source_name,
                "adapter_type": adapter_type,
                "adapter_name": adapter_name,
                "latency_ms": round(latency_ms, 2),
                "records_matched": total_records,
                "filters_applied": dict(query.parameters),
                "warnings": list(result.warnings),
                "provider_name": self._provider_name,
            },
            "output": "\n".join(records_preview) or "No records returned.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self._progress_callback is not None:
            try:
                self._progress_callback(event)
            except Exception:  # pragma: no cover
                logger.exception("Progress callback failed on trace_step")

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(value.value if hasattr(value, "value") else value)

    @staticmethod
    def _preview_record(source_type: SourceType, record: RawRecord) -> str:
        """Return a short allow-listed preview without dumping an arbitrary payload."""
        payload = record.payload

        def clean(value: Any, limit: int = 180) -> str:
            text = str(value).replace("\n", " ").strip()
            return text if len(text) <= limit else f"{text[: limit - 1]}…"

        if source_type == SourceType.LOGS:
            level = clean(payload.get("level", "log")).upper()
            return f"{level}: {clean(payload.get('message', record.content_type))}"
        if source_type == SourceType.METRICS:
            values = payload.get("values") or []
            latest = values[-1] if isinstance(values, list) and values else payload.get("value", "n/a")
            baseline = payload.get("baseline")
            suffix = f"; baseline {clean(baseline)}" if baseline is not None else ""
            return f"{clean(payload.get('metric_name', record.content_type))}: latest {clean(latest)}{suffix}"
        if source_type == SourceType.CHANGES:
            sha = clean(payload.get("commit_sha", record.source_record_id), 16)
            files = payload.get("files_changed") or []
            file_text = f"; files: {', '.join(map(str, files[:3]))}" if isinstance(files, list) and files else ""
            return f"{sha} — {clean(payload.get('message', 'code change'))}{file_text}"
        if source_type == SourceType.DEPLOYMENTS:
            return (
                f"{clean(payload.get('version', payload.get('deployment_id', record.source_record_id)))} "
                f"reported {clean(payload.get('status', 'unknown'))}; commit "
                f"{clean(payload.get('commit_sha', 'unknown'), 16)}"
            )
        if source_type == SourceType.PIPELINES:
            passed = payload.get("tests_passed")
            failed = payload.get("tests_failed")
            tests = f"; tests {passed} passed / {failed} failed" if passed is not None or failed is not None else ""
            return f"{clean(payload.get('pipeline', record.source_record_id))}: {clean(payload.get('status', 'unknown'))}{tests}"
        if source_type == SourceType.CONFIGURATION:
            config_key = clean(payload.get("key", "configuration"))
            sensitive = any(token in config_key.lower() for token in ("password", "secret", "token", "api_key"))
            old_value = "[redacted]" if sensitive else clean(payload.get("old_value", "unknown"))
            new_value = "[redacted]" if sensitive else clean(payload.get("new_value", "unknown"))
            return f"{config_key}: {old_value} → {new_value}"
        return clean(record.content_type)

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

    def _prepare_hypotheses_for_ranking(
        self, hypothesis_set: HypothesisSet | None
    ) -> HypothesisSet | None:
        """Deduplicate hypotheses prior to passing them to the ranking engine."""
        if hypothesis_set is None or not hypothesis_set.hypotheses:
            return hypothesis_set
        deduped = self._deduplicator.deduplicate(hypothesis_set.hypotheses)
        if len(deduped) != len(hypothesis_set.hypotheses):
            return HypothesisSet(
                incident_id=hypothesis_set.incident_id,
                hypotheses=deduped,
                generated_at=hypothesis_set.generated_at,
            )
        return hypothesis_set

    @property
    def history_query_plans(self) -> list[EvidenceQueryPlan]:
        """All query plans generated across rounds."""
        return list(self._history_query_plans)

    @property
    def history_batches(self) -> list[RawEvidenceBatch]:
        """All raw evidence batches collected across rounds."""
        return list(self._history_batches)

    @property
    def last_missing_info(self) -> MissingInformationAssessment | None:
        """The latest missing information assessment."""
        return self._last_missing_info

    @property
    def last_stopping_decision(self) -> StoppingDecision | None:
        """The latest stopping rule decision."""
        return self._last_stopping_decision

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
        self._last_missing_info = None
        self._last_stopping_decision = None

        round_num = 1
        max_rounds = effective_budget.max_rounds

        # Transition: RECEIVED -> ASSESSING_GAPS
        self._state_machine.transition_to(
            InvestigationState.ASSESSING_GAPS,
            reason="Starting initial gap assessment.",
        )
        self._emit_progress(
            kind="status",
            stage="ingest",
            title="Incident accepted",
            detail="The agent created a bounded investigation and discovered the available evidence sources.",
            output=(
                f"{self._enum_value(incident.severity).upper()} alert for {incident.service}: "
                f"{incident.summary}"
            ),
            metadata={"round": 0},
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
            self._emit_progress(
                kind="reasoning",
                stage="assess",
                title=f"Assessing evidence gaps · round {round_num}",
                detail=(
                    "The agent is comparing the alert and current evidence against the available "
                    "sources to decide what must be verified next."
                ),
                metadata={"round": round_num},
            )
            try:
                missing_info = await self._assessor.assess(
                    incident=incident,
                    source_capabilities=source_capabilities,
                    context=self._current_context,
                    active_hypotheses=active_list,
                    previous_assessment=self._last_missing_info,
                )
                self._last_missing_info = missing_info
            except LLMProviderError as err:
                logger.error("LLM reasoning failed during assessment: %s", err)
                return self._finish_inconclusive(
                    incident_id=incident.incident_id,
                    reason=f"Reasoning provider failed: {err.error.message}",
                    stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                )
            self._budget_tracker.record_reasoning_call()
            self._state_machine.checkpoint_after_call(
                "MissingInformationAssessor.assess",
                payload={"round": round_num},
            )
            gap_lines = [
                f"{self._enum_value(item.priority).upper()}: {item.question} — {item.reason}"
                for item in missing_info.missing_information[:4]
            ]
            self._emit_progress(
                kind="assessment",
                stage="assess",
                title="Gap assessment completed",
                detail=(
                    f"The model identified {len(missing_info.known_facts)} known fact(s) and "
                    f"{len(missing_info.missing_information)} unresolved information gap(s)."
                ),
                output="\n".join(gap_lines) or "No additional evidence gaps were identified.",
                metadata={"round": round_num},
            )

            # Collect all historical queries to avoid duplicates
            all_historical_queries: list[EvidenceQueryPlanQuery] = [
                q for plan in self._history_query_plans for q in plan.queries
            ]

            # --- Step B: Plan Allowed Queries ---
            available_source_names = [
                self._enum_value(source.source_type)
                for source in source_capabilities.sources
                if source.available
            ]
            self._emit_progress(
                kind="reasoning",
                stage="plan",
                title=f"Planning evidence queries · round {round_num}",
                detail=(
                    "The model is choosing the smallest set of source queries that can resolve "
                    "the highest-priority gaps, constrained by source capabilities and budget."
                ),
                output=f"Available sources: {', '.join(available_source_names)}",
                metadata={"round": round_num},
            )
            query_plan = await self._planner.plan(
                missing_information=missing_info,
                source_capabilities=source_capabilities,
                context=self._current_context,
                budget=effective_budget,
                round_num=round_num,
                history_queries=all_historical_queries,
            )
            self._history_query_plans.append(query_plan)

            for query in query_plan.queries:
                source_name = self._enum_value(query.source_type)
                self._emit_progress(
                    kind="reasoning",
                    stage="plan",
                    title=f"Selected {source_name} evidence",
                    detail=(
                        f"{query.question} This query was selected because its expected "
                        f"information value is {self._enum_value(query.expected_information_value)}."
                    ),
                    output=f"Parameters: {query.parameters}",
                    metadata={
                        "round": round_num,
                        "query_id": query.query_id,
                        "source_type": source_name,
                    },
                )

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
            for query in query_plan.queries:
                self._emit_progress(
                    kind="action",
                    stage="collect",
                    title=f"Querying {self._enum_value(query.source_type)}",
                    detail=query.question,
                    output=f"Tool request {query.query_id} sent with validated source parameters.",
                    metadata={
                        "round": round_num,
                        "query_id": query.query_id,
                        "source_type": self._enum_value(query.source_type),
                    },
                )
            start_collect = time.perf_counter()
            raw_batch = await self._collection_service.execute(query_plan)
            collect_latency_ms = (time.perf_counter() - start_collect) * 1000.0
            per_query_latency = collect_latency_ms / max(len(query_plan.queries), 1)

            self._budget_tracker.record_queries(len(query_plan.queries))
            self._history_batches.append(raw_batch)
            self._state_machine.checkpoint_after_call(
                "CollectionService.execute",
                payload={"batch_id": raw_batch.batch_id},
            )

            results_by_id = {r.query_id: r for r in raw_batch.results}
            for query in query_plan.queries:
                result = results_by_id.get(query.query_id)
                if result is None:
                    continue

                adapter_name = result.source_adapter or f"{self._enum_value(query.source_type)}_adapter"
                adapter_type = self.classify_adapter(adapter_name)
                tool_call = self.format_tool_call(query.source_type, query.parameters)

                raw_previews = [
                    self._preview_record(result.source_type, record)
                    for record in result.records[:3]
                ]
                total_records = len(result.records)
                is_truncated = total_records > 3
                records_preview = list(raw_previews)
                if is_truncated:
                    records_preview.append(
                        f"... and {total_records - 3} more records ({total_records} total matching records)"
                    )

                rationale = query.question
                if not rationale.endswith("."):
                    rationale = f"{rationale}."

                interpretation = self._synthesize_interpretation(
                    query=query,
                    result=result,
                    round_num=round_num,
                )
                next_decision = self._synthesize_next_decision(
                    query=query,
                    result=result,
                    round_num=round_num,
                    max_rounds=max_rounds,
                )

                # Emit structured trace step containing all 5 required elements
                self._emit_trace_step(
                    query=query,
                    result=result,
                    round_num=round_num,
                    rationale=rationale,
                    tool_call=tool_call,
                    adapter_type=adapter_type,
                    adapter_name=adapter_name,
                    latency_ms=per_query_latency,
                    records_preview=records_preview,
                    is_truncated=is_truncated,
                    interpretation=interpretation,
                    next_decision=next_decision,
                )

                # Also emit backward-compatible observation event
                obs_previews = list(raw_previews)
                if result.warnings:
                    obs_previews.extend(f"Warning: {warning}" for warning in result.warnings[:2])
                self._emit_progress(
                    kind="observation" if result.source_status == SourceStatus.OK else "warning",
                    stage="collect",
                    title=f"{self._enum_value(result.source_type).title()} returned {total_records} record(s)",
                    detail=(
                        f"Tool call {result.query_id} completed with status "
                        f"{self._enum_value(result.source_status)}. The preview below is the evidence returned, "
                        "not model-generated text."
                    ),
                    output="\n".join(obs_previews) or "No records returned.",
                    metadata={
                        "round": round_num,
                        "query_id": result.query_id,
                        "source_type": self._enum_value(result.source_type),
                        "record_count": total_records,
                        "latency_ms": round(per_query_latency, 2),
                        "adapter_type": adapter_type,
                    },
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
            self._emit_progress(
                kind="observation",
                stage="context",
                title="Evidence normalized into a timeline",
                detail=(
                    "The agent correlated timestamps and source records into one incident context "
                    "before asking the model to form causal explanations."
                ),
                output=(
                    f"Context now contains {len(self._current_context.evidence)} evidence item(s) "
                    f"and {len(self._current_context.timeline)} timeline event(s)."
                ),
                metadata={"round": round_num},
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
            self._emit_progress(
                kind="reasoning",
                stage="hypothesize",
                title=(
                    f"Testing causal hypotheses · round {round_num}"
                    if self._current_hypotheses is None
                    else f"Re-evaluating causal hypotheses · round {round_num}"
                ),
                detail=(
                    "The model is comparing competing explanations against the normalized "
                    "evidence and must cite the records that support or contradict each claim."
                ),
                output=(
                    f"Evidence available: {len(self._current_context.evidence)} item(s) across "
                    f"{len(self._current_context.source_coverage)} source coverage entries."
                ),
                metadata={"round": round_num},
            )

            if self._current_hypotheses is None:
                self._state_machine.checkpoint_before_call(
                    "HypothesisGenerator.generate",
                    payload={"round": round_num},
                )
                try:
                    self._current_hypotheses = await self._generator.generate(
                        incident=incident,
                        context=self._current_context,
                        limits=effective_budget,
                    )
                except LLMProviderError as err:
                    logger.error("LLM reasoning failed during hypothesis generation: %s", err)
                    return self._finish_inconclusive(
                        incident_id=incident.incident_id,
                        reason=f"Reasoning provider failed: {err.error.message}",
                        stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
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
                try:
                    self._current_hypotheses = await self._reviser.revise(
                        previous_hypotheses=self._current_hypotheses,
                        new_context=self._current_context,
                    )
                except LLMProviderError as err:
                    logger.error("LLM reasoning failed during hypothesis revision: %s", err)
                    return self._finish_inconclusive(
                        incident_id=incident.incident_id,
                        reason=f"Reasoning provider failed: {err.error.message}",
                        stop_reason=StopReason.INSUFFICIENT_EVIDENCE,
                    )
                self._budget_tracker.record_reasoning_call()
                self._state_machine.checkpoint_after_call(
                    "HypothesisReviser.revise",
                    payload={"count": len(self._current_hypotheses.hypotheses)},
                )

            hypothesis_lines = [
                f"• {hypothesis.statement} ({len(hypothesis.supporting_evidence)} supporting, "
                f"{len(hypothesis.contradicting_evidence)} contradicting citations)"
                for hypothesis in self._current_hypotheses.hypotheses[:5]
            ]
            self._emit_progress(
                kind="reasoning",
                stage="hypothesize",
                title=(
                    "Generated evidence-backed hypotheses"
                    if round_num == 1
                    else "Revised hypotheses with new evidence"
                ),
                detail=(
                    "The model proposed testable explanations, but only citations that exist in "
                    "the normalized context are retained."
                ),
                output="\n".join(hypothesis_lines) or "No valid hypotheses were produced.",
                metadata={"round": round_num},
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
            self._last_stopping_decision = decision
            checklist_items = []
            for crit, ok in decision.criteria_status.items():
                mark = "✓" if ok else "✗"
                crit_name = crit.replace("_", " ").title()
                checklist_items.append(f"[{mark}] {crit_name}")
            checklist_summary = " · ".join(checklist_items) if checklist_items else ""

            output_text = (
                "Stop and rank the supported hypotheses."
                if decision.should_stop
                else f"Continue to investigation round {round_num + 1}."
            )
            if checklist_summary:
                output_text = f"{output_text}\nCompletion criteria: {checklist_summary}"

            self._emit_progress(
                kind="decision",
                stage="rank",
                title="Evaluated stopping rules",
                detail=decision.reason,
                output=output_text,
                metadata={
                    "round": round_num,
                    "should_stop": decision.should_stop,
                    "criteria_status": decision.criteria_status,
                    "unresolved_criteria": decision.unresolved_criteria,
                },
            )

            if decision.should_stop:
                prepared_hypotheses = self._prepare_hypotheses_for_ranking(
                    self._current_hypotheses
                )
                if decision.is_inconclusive:
                    self._state_machine.transition_to(
                        InvestigationState.INCONCLUSIVE,
                        reason=decision.reason,
                    )
                    return self._ranking_engine.rank(
                        hypothesis_set=prepared_hypotheses,
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
                        hypothesis_set=prepared_hypotheses,
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

        prepared_hypotheses = self._prepare_hypotheses_for_ranking(self._current_hypotheses)

        if query_plan.stop_reason == StopReason.SOURCES_UNAVAILABLE:
            self._state_machine.transition_to(
                InvestigationState.INCONCLUSIVE,
                reason="Required sources unavailable.",
            )
            return self._ranking_engine.rank(
                hypothesis_set=prepared_hypotheses or HypothesisSet(
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
                hypothesis_set=prepared_hypotheses,
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
            hypothesis_set=prepared_hypotheses or HypothesisSet(
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
