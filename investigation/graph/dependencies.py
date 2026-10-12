"""Runtime-only dependencies injected into investigation graph nodes."""

from __future__ import annotations

from dataclasses import dataclass

from investigation.graph.ports import (
    Clock,
    CollectionService,
    ContextBuilder,
    HypothesisService,
    MissingInformationService,
    ProgressSink,
    QueryPlanningService,
    RankingService,
    RemediationPlanningService,
    StoppingService,
)


@dataclass(frozen=True, slots=True)
class GraphDependencies:
    collection_service: CollectionService
    context_builder: ContextBuilder
    missing_information_service: MissingInformationService
    query_planning_service: QueryPlanningService
    hypothesis_service: HypothesisService
    stopping_service: StoppingService
    ranking_service: RankingService
    progress_sink: ProgressSink
    clock: Clock
    remediation_planning_service: RemediationPlanningService | None = None


__all__ = ["GraphDependencies"]
