"""Serializable state exchanged by RecoverIT LangGraph nodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from contracts.remediation.schemas import RemediationPlan

from contracts.collection.schemas import (
    EvidenceQueryPlan,
    RawEvidenceBatch,
    SourceCapabilityCatalog,
)
from contracts.errors.schemas import ProgressEvent, StructuredError
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import HypothesisSet, RankedHypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    BudgetUsage,
    InvestigationBudget,
    MissingInformationAssessment,
    StopDecision,
)
from contracts.enums import InvestigationState


class InvestigationInput(TypedDict):
    """Public input accepted by the compiled investigation graph."""

    incident: IncidentSeed
    source_capabilities: SourceCapabilityCatalog
    budget: InvestigationBudget


class InvestigationGraphState(InvestigationInput, total=False):
    """Complete checkpoint-safe state used inside the graph."""

    workflow_state: InvestigationState
    started_at: datetime
    round_number: int
    budget_usage: BudgetUsage
    missing_information: MissingInformationAssessment | None
    query_plan: EvidenceQueryPlan | None
    query_history: list[EvidenceQueryPlan]
    latest_batch: RawEvidenceBatch | None
    batch_history: list[RawEvidenceBatch]
    context: IncidentContextSnapshot
    hypotheses: HypothesisSet | None
    stop_decision: StopDecision | None
    ranked_result: RankedHypothesisSet | None
    errors: list[StructuredError]
    progress_events: list[ProgressEvent]
    remediation_plan: RemediationPlan | None


class InvestigationOutput(TypedDict):
    """Stable graph result consumed by the application runner."""

    ranked_result: RankedHypothesisSet
    context: IncidentContextSnapshot
    query_history: list[EvidenceQueryPlan]
    batch_history: list[RawEvidenceBatch]
    errors: list[StructuredError]
    progress_events: list[ProgressEvent]
    remediation_plan: RemediationPlan | None


__all__ = [
    "InvestigationGraphState",
    "InvestigationInput",
    "InvestigationOutput",
]
