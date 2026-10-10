"""Canonical HypothesisService satisfying the frozen graph protocol."""

from __future__ import annotations

from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import HypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    InvestigationBudget,
    MissingInformationAssessment,
)
from reasoning.hypotheses.citation_validator import CitationValidator
from reasoning.hypotheses.deduplicator import HypothesisDeduplicator
from reasoning.hypotheses.generator import HypothesisGenerator
from reasoning.hypotheses.reviser import HypothesisReviser
from reasoning.provider.interface import ReasoningProvider


class DefaultHypothesisService:
    """Canonical HypothesisService satisfying the frozen graph protocol.

    Coordinates hypothesis generation and revision with citation validation,
    bias mitigation, and deduplication.
    """

    def __init__(
        self,
        provider: ReasoningProvider,
        generator: HypothesisGenerator | None = None,
        reviser: HypothesisReviser | None = None,
        citation_validator: CitationValidator | None = None,
        deduplicator: HypothesisDeduplicator | None = None,
    ) -> None:
        self._provider = provider
        validator = citation_validator or CitationValidator()
        dedup = deduplicator or HypothesisDeduplicator()
        self._generator = generator or HypothesisGenerator(
            provider, citation_validator=validator, deduplicator=dedup
        )
        self._reviser = reviser or HypothesisReviser(
            provider, citation_validator=validator, deduplicator=dedup
        )

    @property
    def provider(self) -> ReasoningProvider:
        return self._provider

    @property
    def generator(self) -> HypothesisGenerator:
        return self._generator

    @property
    def reviser(self) -> HypothesisReviser:
        return self._reviser

    async def generate(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        budget: InvestigationBudget,
    ) -> HypothesisSet:
        """Generate hypotheses for the incident using the current context."""
        return await self._generator.generate(
            incident=incident,
            context=context,
            limits=budget,
        )

    async def revise(
        self,
        incident: IncidentSeed,
        previous_hypotheses: HypothesisSet,
        context: IncidentContextSnapshot,
        assessment: MissingInformationAssessment,
    ) -> HypothesisSet:
        """Revise hypotheses reflecting newly gathered evidence and assessed gaps."""
        return await self._reviser.revise(
            previous_hypotheses=previous_hypotheses,
            new_context=context,
            incident=incident,
            assessment=assessment,
        )
