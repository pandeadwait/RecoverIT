"""
Hypothesis reviser component.

Revises hypotheses as new evidence arrives in subsequent investigation rounds.
Updates revision counters, manages status transitions (active, weakened, rejected),
preserves all rejected hypotheses, and re-validates citations against latest context.

See WORK_DIVISION.md §8.5, §8.10 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts.common import HypothesisStatus
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import EvidenceCitation, Hypothesis, HypothesisSet
from reasoning.hypotheses.citation_validator import CitationValidator
from reasoning.hypotheses.deduplicator import HypothesisDeduplicator
from reasoning.provider.interface import ReasoningProvider

logger = logging.getLogger(__name__)


class HypothesisReviser:
    """
    Revises hypotheses against newly collected evidence.

    Guarantees:
    - Calls ReasoningProvider.revise_hypotheses().
    - Updates revision numbers monotonically (at least prev.revision + 1).
    - Supports status transitions: active, weakened, rejected.
    - Deduplicates semantically equivalent hypotheses while preserving distinct ones.
    - Preserves all rejected hypotheses (never drops them).
    - Preserves previous hypotheses omitted by the provider.
    - Re-validates citations against the latest IncidentContextSnapshot.
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

    async def revise(
        self,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        """
        Produce a revised HypothesisSet reflecting newly gathered evidence.
        """
        raw_revised = await self._provider.revise_hypotheses(
            previous_hypotheses=previous_hypotheses,
            new_context=new_context,
        )

        return self.validate_revision(
            revised_set=raw_revised,
            previous_hypotheses=previous_hypotheses,
            new_context=new_context,
        )

    def validate_revision(
        self,
        revised_set: HypothesisSet,
        previous_hypotheses: HypothesisSet,
        new_context: IncidentContextSnapshot,
    ) -> HypothesisSet:
        """
        Validate, re-number revisions, and preserve rejected hypotheses.
        """
        prev_map: dict[str, Hypothesis] = {
            h.hypothesis_id: h for h in previous_hypotheses.hypotheses
        }
        context_evidence_ids = {e.evidence_id for e in new_context.evidence}

        processed_ids: set[str] = set()
        validated_hypotheses: list[Hypothesis] = []

        for h in revised_set.hypotheses:
            prev = prev_map.get(h.hypothesis_id)
            if prev is not None:
                # Monotonically increment revision
                revision = max(h.revision, prev.revision + 1)
                # If previously rejected, it remains rejected
                status = (
                    HypothesisStatus.REJECTED
                    if prev.status == HypothesisStatus.REJECTED
                    else h.status
                )
            else:
                revision = max(1, h.revision)
                status = h.status

            # Re-validate citations against current evidence
            valid_supporting = [
                c for c in h.supporting_evidence
                if c.evidence_id in context_evidence_ids and c.reason and c.reason.strip()
            ]
            valid_contradicting = [
                c for c in h.contradicting_evidence
                if c.evidence_id in context_evidence_ids and c.reason and c.reason.strip()
            ]

            cleaned = Hypothesis(
                hypothesis_id=h.hypothesis_id,
                incident_id=previous_hypotheses.incident_id,
                revision=revision,
                statement=h.statement,
                root_cause_category=h.root_cause_category,
                affected_component=h.affected_component,
                supporting_evidence=valid_supporting,
                contradicting_evidence=valid_contradicting,
                missing_information_ids=h.missing_information_ids,
                testable_prediction=h.testable_prediction,
                status=status,
            )

            report = self._citation_validator.validate_hypothesis(cleaned, new_context)
            if not report.is_valid:
                logger.warning(
                    "Revised hypothesis '%s' citation issues: %s",
                    h.hypothesis_id,
                    report.errors,
                )

            validated_hypotheses.append(cleaned)
            processed_ids.add(h.hypothesis_id)

        # Preserve any previously rejected hypotheses that were omitted from raw_revised
        for prev_id, prev_h in prev_map.items():
            if prev_id not in processed_ids and prev_h.status == HypothesisStatus.REJECTED:
                preserved = Hypothesis(
                    hypothesis_id=prev_h.hypothesis_id,
                    incident_id=prev_h.incident_id,
                    revision=prev_h.revision + 1,
                    statement=prev_h.statement,
                    root_cause_category=prev_h.root_cause_category,
                    affected_component=prev_h.affected_component,
                    supporting_evidence=prev_h.supporting_evidence,
                    contradicting_evidence=prev_h.contradicting_evidence,
                    missing_information_ids=prev_h.missing_information_ids,
                    testable_prediction=prev_h.testable_prediction,
                    status=HypothesisStatus.REJECTED,
                )
                validated_hypotheses.append(preserved)
                processed_ids.add(prev_id)

        # Deduplicate semantically equivalent hypotheses
        deduplicated_hypotheses = self._deduplicator.deduplicate(validated_hypotheses)

        return HypothesisSet(
            incident_id=previous_hypotheses.incident_id,
            hypotheses=deduplicated_hypotheses,
            generated_at=datetime.now(timezone.utc),
        )
