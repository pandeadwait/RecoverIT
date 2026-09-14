"""
Missing information assessment component.

Coordinates with a ReasoningProvider to identify established facts,
unresolved gaps, and unavailable information for an incident.
Validates that output references existing evidence and available sources.

See WORK_DIVISION.md §8.5 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.common import (
    InformationGapCategory,
    InformationPriority,
    SourceType,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import Hypothesis
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    KnownFact,
    MissingInformationAssessment,
    MissingInformationItem,
)
from reasoning.provider.interface import ReasoningProvider

logger = logging.getLogger(__name__)


class MissingInformationAssessor:
    """
    Assesses information gaps for an investigation round.

    Guarantees:
    - Calls ReasoningProvider.assess_missing_information().
    - Validates known_facts to reference only existing evidence IDs from the context.
    - Validates missing_information items have candidate_sources present in the catalog.
    - Moves items with no available sources into unavailable_information.
    - Ensures priority uses allowed InformationPriority values.
    - Carries forward unresolved HIGH priority items from previous round assessments.
    - Operates correctly on an empty initial context.
    """

    def __init__(self, provider: ReasoningProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> ReasoningProvider:
        """The underlying reasoning provider."""
        return self._provider

    async def assess(
        self,
        incident: IncidentSeed,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        active_hypotheses: list[Hypothesis] | None = None,
        previous_assessment: MissingInformationAssessment | None = None,
    ) -> MissingInformationAssessment:
        """
        Assess current investigative progress and identify remaining gaps.
        """
        active = active_hypotheses or []

        raw = await self._provider.assess_missing_information(
            incident=incident,
            source_capabilities=source_capabilities,
            context=context,
            active_hypotheses=active,
        )

        return self.validate_assessment(
            assessment=raw,
            source_capabilities=source_capabilities,
            context=context,
            previous_assessment=previous_assessment,
        )

    def validate_assessment(
        self,
        assessment: MissingInformationAssessment,
        source_capabilities: SourceCapabilityCatalog,
        context: IncidentContextSnapshot,
        previous_assessment: MissingInformationAssessment | None = None,
    ) -> MissingInformationAssessment:
        """
        Validate and sanitize a MissingInformationAssessment against current context and catalog.
        """
        valid_evidence_ids = {e.evidence_id for e in context.evidence}

        available_sources: set[SourceType] = {
            s.source_type for s in source_capabilities.sources if s.available
        }
        all_catalog_sources: set[SourceType] = {
            s.source_type for s in source_capabilities.sources
        }

        # 1. Validate known facts: only reference existing evidence IDs
        validated_known_facts: list[KnownFact] = []
        for fact in assessment.known_facts:
            sanitized_ids = [eid for eid in fact.evidence_ids if eid in valid_evidence_ids]
            validated_known_facts.append(
                KnownFact(
                    statement=fact.statement,
                    evidence_ids=sanitized_ids,
                )
            )

        # 2. Validate missing information items and candidate sources
        validated_missing: list[MissingInformationItem] = []
        unavailable_items: list[MissingInformationItem] = list(assessment.unavailable_information)

        for item in assessment.missing_information:
            # Validate priority
            priority = self._normalize_priority(item.priority)
            category = getattr(item, "category", InformationGapCategory.DIRECT_CAUSAL_EVIDENCE)

            # Filter candidate sources: must be present and available in catalog.
            # If the provider omitted candidate_sources or left it empty, consider all available sources.
            if not item.candidate_sources:
                available_candidates = list(available_sources)
            else:
                available_candidates = [
                    s for s in item.candidate_sources if s in available_sources
                ]

            if not available_candidates:
                # If sources are in catalog but unavailable, or not in catalog at all,
                # the item is unavailable
                unavailable_items.append(
                    MissingInformationItem(
                        information_id=item.information_id,
                        question=item.question,
                        reason=item.reason,
                        priority=priority,
                        category=category,
                        candidate_sources=[
                            s for s in item.candidate_sources if s in all_catalog_sources
                        ],
                        resolved=False,
                    )
                )
            else:
                validated_missing.append(
                    MissingInformationItem(
                        information_id=item.information_id,
                        question=item.question,
                        reason=item.reason,
                        priority=priority,
                        category=category,
                        candidate_sources=available_candidates,
                        resolved=item.resolved,
                    )
                )

        # 3. Carry forward unresolved HIGH priority gaps from previous assessment
        if previous_assessment is not None:
            existing_ids = {item.information_id for item in validated_missing}
            existing_questions = {
                item.question.strip().lower() for item in validated_missing
            }
            for prev_item in previous_assessment.missing_information:
                if (
                    prev_item.priority == InformationPriority.HIGH
                    and not prev_item.resolved
                    and prev_item.information_id not in existing_ids
                    and prev_item.question.strip().lower() not in existing_questions
                ):
                    cand = [s for s in prev_item.candidate_sources if s in available_sources]
                    if cand:
                        validated_missing.append(
                            prev_item.model_copy(update={"candidate_sources": cand})
                        )
                    else:
                        unavailable_items.append(prev_item)

        recommended_stop = assessment.recommended_stop
        # If there are no missing information items left that can be queried,
        # and unavailable items exist, recommend stopping
        if not validated_missing and unavailable_items:
            recommended_stop = True

        return MissingInformationAssessment(
            incident_id=assessment.incident_id,
            assessment_id=assessment.assessment_id,
            known_facts=validated_known_facts,
            missing_information=validated_missing,
            unavailable_information=unavailable_items,
            recommended_stop=recommended_stop,
        )

    @staticmethod
    def _normalize_priority(priority: InformationPriority | str) -> InformationPriority:
        if isinstance(priority, InformationPriority):
            return priority
        if isinstance(priority, str):
            try:
                return InformationPriority(priority.lower())
            except ValueError:
                return InformationPriority.MEDIUM
        return InformationPriority.MEDIUM
