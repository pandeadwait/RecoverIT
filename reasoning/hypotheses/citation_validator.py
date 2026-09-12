"""
Evidence citation validation component.

Validates that hypotheses reference real evidence from the current context,
rejects cross-incident citations, flags unsupported factual claims as assumptions,
detects un-differentiated dual support/contradiction claims, and tracks
truncated or low-reliability evidence.

See WORK_DIVISION.md §8.10 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from pydantic import Field

from contracts.common import ContractModel, Reliability
from contracts.errors.schemas import (
    CITATION_INVALID,
    CROSS_INCIDENT_REFERENCE,
    StructuredError,
)
from contracts.evidence.schemas import EvidenceQuality, IncidentContextSnapshot
from contracts.hypothesis.schemas import EvidenceCitation, Hypothesis

logger = logging.getLogger(__name__)


class EvidenceRepositoryReader(Protocol):
    """Optional protocol for looking up evidence outside the immediate snapshot."""

    def get_evidence(self, evidence_id: str) -> Any | None:
        """Fetch an evidence record by ID."""
        ...


class CitationValidationReport(ContractModel):
    """Comprehensive validation result for hypothesis citations."""
    is_valid: bool = Field(
        ...,
        description="Whether all citations pass strict validity checks.",
    )
    errors: list[StructuredError] = Field(
        default_factory=list,
        description="Errors that invalidate a citation or hypothesis.",
    )
    warnings: list[StructuredError] = Field(
        default_factory=list,
        description="Non-fatal warnings or flagged items.",
    )
    low_reliability_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Cited evidence items with low reliability.",
    )
    truncated_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Cited evidence items that were truncated at collection.",
    )
    uncited_assumptions: list[str] = Field(
        default_factory=list,
        description="Hypotheses or claims lacking empirical evidence citations.",
    )


class CitationValidator:
    """
    Validates evidence citations for root-cause hypotheses.

    Guarantees:
    - Every cited evidence_id must exist in the supplied context or repository.
    - Evidence from another incident is strictly rejected.
    - Conflicting dual citations (both support and contradiction) require distinct, explanatory reasons.
    - Citations without reasons are rejected.
    - Truncated or low-reliability evidence items are flagged.
    - Hypotheses with zero supporting evidence are marked as assumptions.
    """

    def __init__(
        self,
        repository: EvidenceRepositoryReader | None = None,
    ) -> None:
        self._repository = repository

    def validate_hypothesis(
        self,
        hypothesis: Hypothesis,
        context: IncidentContextSnapshot,
    ) -> CitationValidationReport:
        """
        Validate all supporting and contradicting citations for a single hypothesis.
        """
        errors: list[StructuredError] = []
        warnings: list[StructuredError] = []
        low_rel_ids: list[str] = []
        trunc_ids: list[str] = []
        assumptions: list[str] = []

        context_evidence_map = {e.evidence_id: e for e in context.evidence}

        # 1. Uncited claim check
        if not hypothesis.supporting_evidence:
            assumptions.append(
                f"Hypothesis '{hypothesis.hypothesis_id}' has no supporting evidence; treated as assumption."
            )
            warnings.append(
                StructuredError(
                    code=CITATION_INVALID,
                    message=f"Hypothesis '{hypothesis.hypothesis_id}' contains no supporting evidence citations.",
                    retryable=False,
                    source="reasoning.hypotheses.citation_validator",
                    details={"hypothesis_id": hypothesis.hypothesis_id},
                )
            )

        # 2. Check each supporting citation
        supp_map: dict[str, EvidenceCitation] = {}
        for cit in hypothesis.supporting_evidence:
            self._validate_citation_item(
                cit=cit,
                hypothesis=hypothesis,
                context=context,
                context_evidence_map=context_evidence_map,
                citation_type="supporting",
                errors=errors,
                low_rel_ids=low_rel_ids,
                trunc_ids=trunc_ids,
            )
            supp_map[cit.evidence_id] = cit

        # 3. Check each contradicting citation
        contra_map: dict[str, EvidenceCitation] = {}
        for cit in hypothesis.contradicting_evidence:
            self._validate_citation_item(
                cit=cit,
                hypothesis=hypothesis,
                context=context,
                context_evidence_map=context_evidence_map,
                citation_type="contradicting",
                errors=errors,
                low_rel_ids=low_rel_ids,
                trunc_ids=trunc_ids,
            )
            contra_map[cit.evidence_id] = cit

        # 4. Detect dual support/contradiction conflict without explanation
        overlapping_ids = set(supp_map.keys()) & set(contra_map.keys())
        for eid in overlapping_ids:
            supp_cit = supp_map[eid]
            contra_cit = contra_map[eid]

            # If identical reasons or no distinct reasoning, reject
            if (
                supp_cit.reason.strip().lower() == contra_cit.reason.strip().lower()
                or not self._has_dual_citation_explanation(supp_cit.reason, contra_cit.reason)
            ):
                errors.append(
                    StructuredError(
                        code=CITATION_INVALID,
                        message=(
                            f"Evidence '{eid}' cited as both supporting and contradicting "
                            f"without an explicit differentiating explanation."
                        ),
                        retryable=False,
                        source="reasoning.hypotheses.citation_validator",
                        details={
                            "hypothesis_id": hypothesis.hypothesis_id,
                            "evidence_id": eid,
                            "supporting_reason": supp_cit.reason,
                            "contradicting_reason": contra_cit.reason,
                        },
                    )
                )

        is_valid = len(errors) == 0
        return CitationValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            low_reliability_evidence_ids=sorted(list(set(low_rel_ids))),
            truncated_evidence_ids=sorted(list(set(trunc_ids))),
            uncited_assumptions=assumptions,
        )

    def _validate_citation_item(
        self,
        cit: EvidenceCitation,
        hypothesis: Hypothesis,
        context: IncidentContextSnapshot,
        context_evidence_map: dict[str, Any],
        citation_type: str,
        errors: list[StructuredError],
        low_rel_ids: list[str],
        trunc_ids: list[str],
    ) -> None:
        # A. Reason must be non-empty
        if not cit.reason or not cit.reason.strip():
            errors.append(
                StructuredError(
                    code=CITATION_INVALID,
                    message=f"{citation_type.capitalize()} citation for evidence '{cit.evidence_id}' lacks a reason.",
                    retryable=False,
                    source="reasoning.hypotheses.citation_validator",
                    details={
                        "hypothesis_id": hypothesis.hypothesis_id,
                        "evidence_id": cit.evidence_id,
                    },
                )
            )

        # B. Cross-incident check
        if self._is_cross_incident(cit.evidence_id, hypothesis.incident_id):
            errors.append(
                StructuredError(
                    code=CROSS_INCIDENT_REFERENCE,
                    message=(
                        f"Evidence ID '{cit.evidence_id}' references an external incident "
                        f"and is not valid for incident '{hypothesis.incident_id}'."
                    ),
                    retryable=False,
                    source="reasoning.hypotheses.citation_validator",
                    details={
                        "hypothesis_id": hypothesis.hypothesis_id,
                        "evidence_id": cit.evidence_id,
                        "expected_incident_id": hypothesis.incident_id,
                    },
                )
            )
            return

        # C. Existence check
        record = context_evidence_map.get(cit.evidence_id)
        if record is None and self._repository is not None:
            repo_record = self._repository.get_evidence(cit.evidence_id)
            if repo_record is not None:
                # Check cross-incident in repository record if available
                rec_incident_id = getattr(repo_record, "incident_id", None)
                if rec_incident_id and rec_incident_id != hypothesis.incident_id:
                    errors.append(
                        StructuredError(
                            code=CROSS_INCIDENT_REFERENCE,
                            message=f"Evidence '{cit.evidence_id}' belongs to incident '{rec_incident_id}'.",
                            retryable=False,
                            source="reasoning.hypotheses.citation_validator",
                            details={
                                "evidence_id": cit.evidence_id,
                                "incident_id": rec_incident_id,
                            },
                        )
                    )
                    return
                record = repo_record

        if record is None:
            errors.append(
                StructuredError(
                    code=CITATION_INVALID,
                    message=f"Evidence ID '{cit.evidence_id}' does not exist in context or repository.",
                    retryable=False,
                    source="reasoning.hypotheses.citation_validator",
                    details={
                        "hypothesis_id": hypothesis.hypothesis_id,
                        "evidence_id": cit.evidence_id,
                    },
                )
            )
            return

        # D. Track quality metadata (reliability and truncation)
        quality: EvidenceQuality | None = getattr(record, "quality", None)
        if quality is not None:
            if getattr(quality, "reliability", None) == Reliability.LOW:
                low_rel_ids.append(cit.evidence_id)
            if getattr(quality, "truncated_source", False):
                trunc_ids.append(cit.evidence_id)

    @staticmethod
    def _is_cross_incident(evidence_id: str, current_incident_id: str) -> bool:
        """Check if evidence ID encodes an incident ID prefix that doesn't match current."""
        if evidence_id.startswith("inc_") and not evidence_id.startswith(f"{current_incident_id}_"):
            # Check if there is another incident prefix, e.g. inc_002_ev_01 vs inc_001
            parts = evidence_id.split("_")
            if len(parts) >= 2:
                prefix = f"{parts[0]}_{parts[1]}"
                if prefix.startswith("inc_") and prefix != current_incident_id:
                    return True
        return False

    @staticmethod
    def _has_dual_citation_explanation(supp_reason: str, contra_reason: str) -> bool:
        """Check if supporting and contradicting reasons explain different aspects."""
        combined = (supp_reason + " " + contra_reason).lower()
        differentiating_markers = {
            "partially", "however", "timing", "timing difference",
            "before", "after", "contrast", "initial", "subsequent",
            "correlation", "causation", "discrepancy", "exception",
        }
        return any(marker in combined for marker in differentiating_markers)
