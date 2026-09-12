"""
ReasoningProvider interface definition.

Provider-neutral protocol for LLM/heuristic reasoning adapters.
Guarantees that provider-specific response objects never escape the adapter layer.

See WORK_DIVISION.md §8.7 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import Hypothesis, HypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    InvestigationBudget,
    MissingInformationAssessment,
)


@runtime_checkable
class ReasoningProvider(Protocol):
    """
    Abstract reasoning protocol for investigation steps.

    Implementations can be:
    - Live LLM providers (via prompt strategies and structured output parsers)
    - Deterministic fake providers (for testing and offline development)
    - Recorded replay providers (for deterministic regression testing)
    """

    async def assess_missing_information(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis],
    ) -> MissingInformationAssessment:
        """
        Identify established facts and critical information gaps.

        Returns a canonical MissingInformationAssessment object.
        """
        ...

    async def plan_queries(
        self,
        missing_information: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> EvidenceQueryPlan:
        """
        Plan targeted evidence queries within source capabilities and budget limits.

        Returns a canonical EvidenceQueryPlan object.
        """
        ...

    async def generate_hypotheses(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        """
        Generate multiple plausible root-cause hypotheses with evidence citations.

        Returns a canonical HypothesisSet object.
        """
        ...

    async def revise_hypotheses(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        """
        Update, strengthen, weaken, or reject hypotheses based on new evidence.

        Returns an updated HypothesisSet object.
        """
        ...
