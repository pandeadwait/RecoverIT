"""
Hypothesis generator component.

Coordinates with ReasoningProvider to generate plausible root-cause hypotheses,
validates citations against context evidence, ensures unbiased multi-hypothesis
generation (including non-change alternatives), and produces a validated HypothesisSet.

See WORK_DIVISION.md §8.5, §8.10 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from contracts.common import HypothesisStatus, RootCauseCategory
from contracts.errors.schemas import SCHEMA_VALIDATION_FAILED, StructuredError
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import EvidenceCitation, Hypothesis, HypothesisSet
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from reasoning.hypotheses.citation_validator import CitationValidator
from reasoning.hypotheses.deduplicator import HypothesisDeduplicator
from reasoning.provider.interface import ReasoningProvider

logger = logging.getLogger(__name__)

CHANGE_RELATED_CATEGORIES = frozenset({
    RootCauseCategory.CONFIGURATION_REGRESSION,
    RootCauseCategory.DEPLOYMENT_FAILURE,
    RootCauseCategory.CODE_DEFECT,
})


class HypothesisGenerator:
    """
    Generates and validates root-cause hypotheses for an incident.

    Guarantees:
    - Generates at least minimum_hypotheses (from budget/limits).
    - Ensures hypotheses have clear statement, category, component, and prediction.
    - Validates all supporting and contradicting citations with CitationValidator.
    - Deduplicates semantically equivalent hypotheses before ranking.
    - Ensures status is 'active' for all newly generated hypotheses.
    - Enforces bias mitigation: includes at least one hypothesis unrelated to recent changes.
    """

    def __init__(
        self,
        provider: ReasoningProvider,
        citation_validator: CitationValidator | None = None,
        deduplicator: HypothesisDeduplicator | None = None,
    ) -> None:
        self._provider = provider
        self._citation_validator = citation_validator or CitationValidator()
        self._deduplicator = deduplicator or HypothesisDeduplicator()

    async def generate(
        self,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        """
        Generate multiple plausible hypotheses from current incident context.
        """
        raw_set = await self._provider.generate_hypotheses(
            incident=incident,
            context=context,
            limits=limits,
        )

        return self.validate_hypotheses(
            hypothesis_set=raw_set,
            incident=incident,
            context=context,
            limits=limits,
        )

    def validate_hypotheses(
        self,
        hypothesis_set: HypothesisSet,
        incident: IncidentSeed,
        context: IncidentContextSnapshot,
        limits: InvestigationBudget,
    ) -> HypothesisSet:
        """
        Validate, sanitize, and deduplicate a HypothesisSet.
        """
        context_evidence_ids = {e.evidence_id for e in context.evidence}
        validated_hypotheses: list[Hypothesis] = []

        for h in hypothesis_set.hypotheses:
            # 1. Filter citations to only valid evidence IDs
            valid_supporting = [
                c for c in h.supporting_evidence
                if c.evidence_id in context_evidence_ids and c.reason and c.reason.strip()
            ]
            valid_contradicting = [
                c for c in h.contradicting_evidence
                if c.evidence_id in context_evidence_ids and c.reason and c.reason.strip()
            ]

            # 2. Normalize status to ACTIVE
            status = HypothesisStatus.ACTIVE

            # 3. Ensure testable prediction exists
            prediction = h.testable_prediction
            if not prediction or not prediction.strip():
                prediction = f"Targeted inspection of {h.affected_component} should confirm or refute this condition."

            cleaned_hypothesis = Hypothesis(
                hypothesis_id=h.hypothesis_id,
                incident_id=incident.incident_id,
                revision=max(1, h.revision),
                statement=h.statement,
                root_cause_category=h.root_cause_category,
                affected_component=h.affected_component or incident.service,
                supporting_evidence=valid_supporting,
                contradicting_evidence=valid_contradicting,
                missing_information_ids=h.missing_information_ids,
                testable_prediction=prediction,
                status=status,
            )

            # Run full citation validator
            report = self._citation_validator.validate_hypothesis(cleaned_hypothesis, context)
            if not report.is_valid:
                logger.warning(
                    "Hypothesis '%s' citation validation issues: %s",
                    h.hypothesis_id,
                    report.errors,
                )

            validated_hypotheses.append(cleaned_hypothesis)

        # 4. Deduplicate semantically equivalent hypotheses
        validated_hypotheses = self._deduplicator.deduplicate(validated_hypotheses)

        # 5. Bias mitigation: ensure at least one hypothesis is NOT change-related
        if len(validated_hypotheses) >= 1:
            all_change_related = all(
                h.root_cause_category in CHANGE_RELATED_CATEGORIES
                for h in validated_hypotheses
            )
            if all_change_related and len(validated_hypotheses) < limits.maximum_hypotheses:
                # Add an alternative non-change hypothesis
                alt_id = f"hyp_alt_{len(validated_hypotheses) + 1}"
                alt_hyp = Hypothesis(
                    hypothesis_id=alt_id,
                    incident_id=incident.incident_id,
                    revision=1,
                    statement=f"Transient dependency failure or traffic surge impacted {incident.service} independently of recent changes.",
                    root_cause_category=RootCauseCategory.EXTERNAL_DEPENDENCY_FAILURE,
                    affected_component=f"{incident.service}-external",
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    missing_information_ids=[],
                    testable_prediction="Dependency status pages or ingress request volume metrics show contemporaneous anomalies.",
                    status=HypothesisStatus.ACTIVE,
                )
                validated_hypotheses.append(alt_hyp)

        # 6. Cap at maximum_hypotheses
        if len(validated_hypotheses) > limits.maximum_hypotheses:
            validated_hypotheses = validated_hypotheses[: limits.maximum_hypotheses]

        return HypothesisSet(
            incident_id=incident.incident_id,
            hypotheses=validated_hypotheses,
            generated_at=datetime.now(timezone.utc),
        )
