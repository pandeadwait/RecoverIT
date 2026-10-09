"""Frozen service boundaries consumed by the LangGraph workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from contracts.collection.schemas import (
    EvidenceQueryPlan,
    RawEvidenceBatch,
    SourceCapabilityCatalog,
)
from contracts.errors.schemas import ProgressEvent
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.enums import InvestigationStatus, StopReason
from contracts.hypothesis.schemas import HypothesisSet, RankedHypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
    StopDecision,
)


@runtime_checkable
class CollectionService(Protocol):
    async def collect(
        self,
        plan: EvidenceQueryPlan,
        capabilities: SourceCapabilityCatalog,
    ) -> RawEvidenceBatch: ...


@runtime_checkable
class ContextBuilder(Protocol):
    async def build(
        self,
        incident: IncidentSeed,
        batch: RawEvidenceBatch,
        previous_context: IncidentContextSnapshot,
    ) -> IncidentContextSnapshot: ...


@runtime_checkable
class MissingInformationService(Protocol):
    async def assess(
        self,
        incident: IncidentSeed,
        capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        previous_assessment: MissingInformationAssessment | None,
    ) -> MissingInformationAssessment: ...


@runtime_checkable
class QueryPlanningService(Protocol):
    async def plan(
        self,
        assessment: MissingInformationAssessment,
        capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        budget: InvestigationBudget,
        budget_usage: BudgetUsage,
        round_number: int,
        query_history: list[EvidenceQueryPlan],
    ) -> EvidenceQueryPlan: ...


@runtime_checkable
class HypothesisService(Protocol):
    async def generate(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> HypothesisSet: ...

    async def revise(
        self,
        incident: IncidentSeed,
        previous_hypotheses: HypothesisSet,
        context: IncidentContextSnapshot,
        assessment: MissingInformationAssessment,
    ) -> HypothesisSet: ...


@runtime_checkable
class StoppingService(Protocol):
    def evaluate(
        self,
        context: IncidentContextSnapshot,
        hypotheses: HypothesisSet | None,
        assessment: MissingInformationAssessment,
        query_plan: EvidenceQueryPlan,
        budget: InvestigationBudget,
        budget_usage: BudgetUsage,
        round_number: int,
    ) -> StopDecision: ...


@runtime_checkable
class RankingService(Protocol):
    def rank(
        self,
        hypotheses: HypothesisSet,
        context: IncidentContextSnapshot,
        budget_usage: BudgetUsage,
        status: InvestigationStatus,
        stop_reason: StopReason | None,
        remaining_uncertainty: list[str],
    ) -> RankedHypothesisSet: ...


@runtime_checkable
class ProgressSink(Protocol):
    def emit(self, event: ProgressEvent) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "Clock",
    "CollectionService",
    "ContextBuilder",
    "HypothesisService",
    "MissingInformationService",
    "ProgressSink",
    "QueryPlanningService",
    "RankingService",
    "StoppingService",
]
