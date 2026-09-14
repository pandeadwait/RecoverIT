"""
Hypothesis deduplication component.

Identifies and merges semantically equivalent hypotheses before ranking
to ensure diverse, non-redundant explanations are presented to the operator.

See WORK_DIVISION.md §8.5, ARCHITECTURE.md §8, and CREDIBILITY_IMPROVEMENT_PLAN.md Phase 5.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from contracts.common import EvidenceRole, HypothesisStatus, RootCauseCategory
from contracts.hypothesis.schemas import EvidenceCitation, Hypothesis

logger = logging.getLogger(__name__)

STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "cannot", "could", "did", "do", "does",
    "doing", "down", "during", "each", "few", "for", "from", "further", "had",
    "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "if", "in", "into", "is", "it", "its", "itself",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "of", "off",
    "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves",
    "out", "over", "own", "same", "she", "should", "so", "some", "such",
    "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "whom", "why", "with", "would", "you", "your",
    "yours", "yourself", "yourselves",
})

SYNONYM_MAP = {
    "misconfiguration": "config",
    "misconfigured": "config",
    "configuration": "config",
    "configurations": "config",
    "configuring": "config",
    "database": "db",
    "databases": "db",
    "connection": "connect",
    "connections": "connect",
    "connecting": "connect",
    "connected": "connect",
    "exhausted": "exhaust",
    "exhaustion": "exhaust",
    "incorrect": "error",
    "failure": "fail",
    "failed": "fail",
    "failing": "fail",
    "service": "svc",
    "services": "svc",
    "deployment": "deploy",
    "deployments": "deploy",
    "deployed": "deploy",
    "memory": "mem",
    "leak": "leak",
    "leaked": "leak",
    "leaking": "leak",
    "latency": "slow",
    "timeout": "timeout",
    "timeouts": "timeout",
    "incompatible": "incompat",
    "incompatibility": "incompat",
    "upgrade": "update",
    "upgraded": "update",
    "outage": "down",
    "unresponsive": "down",
    "unavailable": "down",
}


class HypothesisDeduplicator:
    """
    Detects and merges semantically duplicate hypotheses.

    Guarantees:
    - Hypotheses with different root-cause categories are never merged.
    - Hypotheses with different components are never merged.
    - Merging preserves all unique supporting and contradicting citations.
    - Stronger citation roles (e.g. CAUSE over CORRELATION) are preserved.
    - Information gaps and testable predictions are aggregated.
    """

    def __init__(self, similarity_threshold: float = 0.50) -> None:
        self._similarity_threshold = similarity_threshold

    @property
    def similarity_threshold(self) -> float:
        return self._similarity_threshold

    @staticmethod
    def normalize_text(text: str) -> set[str]:
        """Tokenize, strip stopwords, apply domain synonyms, and stem tokens."""
        if not text:
            return set()
        cleaned = text.lower().replace("-", " ").replace("_", " ")
        tokens = re.findall(r"\b[a-z0-9]+\b", cleaned)
        normalized = set()
        for tok in tokens:
            if tok in STOP_WORDS:
                continue
            tok = SYNONYM_MAP.get(tok, tok)
            # Basic stemming suffix reduction
            if len(tok) > 4:
                for suffix in ("ing", "ed", "es", "s"):
                    if tok.endswith(suffix) and len(tok) - len(suffix) >= 3:
                        tok = tok[:-len(suffix)]
                        break
            normalized.add(tok)
        return normalized

    def compute_similarity(self, text1: str, text2: str) -> float:
        """
        Compute hybrid containment and Jaccard similarity between two texts.

        Returns a float between 0.0 and 1.0.
        """
        tokens1 = self.normalize_text(text1)
        tokens2 = self.normalize_text(text2)

        if not tokens1 or not tokens2:
            return 1.0 if text1.strip().lower() == text2.strip().lower() else 0.0

        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)

        if union == 0:
            return 0.0

        jaccard = intersection / union
        containment = intersection / min(len(tokens1), len(tokens2))

        # Balanced average of Jaccard and containment
        return (jaccard + containment) / 2.0

    def are_duplicates(
        self,
        h1: Hypothesis,
        h2: Hypothesis,
        threshold: float | None = None,
    ) -> bool:
        """
        Determine whether two hypotheses represent the same underlying explanation.
        """
        # Rule 0: Do not merge rejected hypotheses with non-rejected ones
        s1 = getattr(h1.status, "value", h1.status)
        s2 = getattr(h2.status, "value", h2.status)
        rej = getattr(HypothesisStatus.REJECTED, "value", HypothesisStatus.REJECTED)
        if (s1 == rej) != (s2 == rej):
            return False

        # Rule 1: Root-cause category must match
        cat1 = str(getattr(h1.root_cause_category, "value", h1.root_cause_category))
        cat2 = str(getattr(h2.root_cause_category, "value", h2.root_cause_category))
        if cat1 != cat2:
            return False

        # Rule 2: Affected component must match (normalized)
        comp1 = (h1.affected_component or "").strip().lower().replace("-", "_")
        comp2 = (h2.affected_component or "").strip().lower().replace("-", "_")
        if comp1 != comp2 and comp1 not in comp2 and comp2 not in comp1:
            return False

        # Rule 3: Statement similarity must exceed threshold
        eff_threshold = threshold if threshold is not None else self._similarity_threshold
        similarity = self.compute_similarity(h1.statement, h2.statement)
        return similarity >= eff_threshold

    def merge_hypotheses(self, h1: Hypothesis, h2: Hypothesis) -> Hypothesis:
        """
        Merge two duplicate hypotheses into a single comprehensive hypothesis.
        """
        # Choose preferred statement: keep the longer, more detailed one
        s1 = (h1.statement or "").strip()
        s2 = (h2.statement or "").strip()
        preferred_statement = s1 if len(s1) >= len(s2) else s2

        # Choose preferred prediction: keep the longer, more specific one
        p1 = (h1.testable_prediction or "").strip()
        p2 = (h2.testable_prediction or "").strip()
        preferred_prediction = p1 if len(p1) >= len(p2) else p2

        # Combine supporting evidence with role prioritization
        merged_supporting = self._merge_citation_list(
            h1.supporting_evidence, h2.supporting_evidence
        )

        # Combine contradicting evidence
        merged_contradicting = self._merge_citation_list(
            h1.contradicting_evidence, h2.contradicting_evidence
        )

        # Combine missing information IDs uniquely preserving order
        merged_gaps = list(
            dict.fromkeys(h1.missing_information_ids + h2.missing_information_ids)
        )

        # Retain canonical identifier (earlier alphabetically for determinism)
        canonical_id = min(h1.hypothesis_id, h2.hypothesis_id)
        max_revision = max(h1.revision, h2.revision)

        # Resolve status: ACTIVE > WEAKENED > REJECTED
        status = h1.status
        if h1.status == HypothesisStatus.ACTIVE or h2.status == HypothesisStatus.ACTIVE:
            status = HypothesisStatus.ACTIVE
        elif h1.status == HypothesisStatus.WEAKENED or h2.status == HypothesisStatus.WEAKENED:
            status = HypothesisStatus.WEAKENED
        else:
            status = HypothesisStatus.REJECTED

        return Hypothesis(
            hypothesis_id=canonical_id,
            incident_id=h1.incident_id,
            revision=max_revision,
            statement=preferred_statement,
            root_cause_category=h1.root_cause_category,
            affected_component=h1.affected_component,
            supporting_evidence=merged_supporting,
            contradicting_evidence=merged_contradicting,
            missing_information_ids=merged_gaps,
            testable_prediction=preferred_prediction,
            status=status,
        )

    def deduplicate(
        self,
        hypotheses: Sequence[Hypothesis],
        threshold: float | None = None,
    ) -> list[Hypothesis]:
        """
        Deduplicate a collection of hypotheses, merging duplicates in-place.
        """
        if len(hypotheses) <= 1:
            return list(hypotheses)

        result: list[Hypothesis] = []
        for h in hypotheses:
            merged = False
            for i, existing in enumerate(result):
                if self.are_duplicates(existing, h, threshold=threshold):
                    result[i] = self.merge_hypotheses(existing, h)
                    merged = True
                    logger.debug(
                        "Merged duplicate hypothesis '%s' into '%s'",
                        h.hypothesis_id,
                        result[i].hypothesis_id,
                    )
                    break
            if not merged:
                result.append(h)

        return result

    @staticmethod
    def _merge_citation_list(
        cits1: list[EvidenceCitation],
        cits2: list[EvidenceCitation],
    ) -> list[EvidenceCitation]:
        """Merge two citation lists uniquely by evidence_id, preserving the best reason and role."""
        merged: dict[str, EvidenceCitation] = {}
        for cit in cits1 + cits2:
            if cit.evidence_id not in merged:
                merged[cit.evidence_id] = cit
            else:
                existing = merged[cit.evidence_id]
                # Prioritize CAUSE > CONTRADICTION > EFFECT > CORRELATION > CONTEXT
                preferred_role = existing.role
                if cit.role == EvidenceRole.CAUSE:
                    preferred_role = EvidenceRole.CAUSE
                elif cit.role == EvidenceRole.CONTRADICTION:
                    preferred_role = EvidenceRole.CONTRADICTION
                elif existing.role not in (EvidenceRole.CAUSE, EvidenceRole.CONTRADICTION):
                    if cit.role == EvidenceRole.EFFECT:
                        preferred_role = EvidenceRole.EFFECT
                    elif existing.role not in (EvidenceRole.EFFECT,):
                        preferred_role = cit.role

                preferred_reason = (
                    cit.reason if len(cit.reason) > len(existing.reason) else existing.reason
                )
                merged[cit.evidence_id] = EvidenceCitation(
                    evidence_id=cit.evidence_id,
                    reason=preferred_reason,
                    role=preferred_role,
                )
        return list(merged.values())
