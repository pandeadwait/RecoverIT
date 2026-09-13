"""
Deterministic feature calculators for hypothesis ranking.

All calculators are pure, deterministic functions that take a Hypothesis
and IncidentContextSnapshot and return a normalized float between 0.0 and 1.0.
No calculator calls an LLM or has non-deterministic side-effects.

See WORK_DIVISION.md §8.11 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from contracts.common import EvidenceType, RootCauseCategory, SourceType
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import Hypothesis

CHANGE_RELATED_CATEGORIES = frozenset({
    RootCauseCategory.CONFIGURATION_REGRESSION,
    RootCauseCategory.DEPLOYMENT_FAILURE,
    RootCauseCategory.CODE_DEFECT,
})

SYMPTOM_TYPES = frozenset({
    EvidenceType.ERROR_EVENT,
    EvidenceType.WARNING_EVENT,
    EvidenceType.METRIC_ANOMALY,
})

CHANGE_TYPES = frozenset({
    EvidenceType.CODE_CHANGE,
    EvidenceType.CONFIGURATION_CHANGE,
    EvidenceType.DEPLOYMENT_EVENT,
})


def calculate_independent_source_support(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Calculate diversity of independent source types in supporting evidence.

    Returns:
    - 0.0 for 0 sources
    - 0.5 for 1 source
    - 0.85 for 2 sources
    - 1.0 for 3 or more distinct sources
    """
    if not hypothesis.supporting_evidence or not context.evidence:
        return 0.0

    context_map = {e.evidence_id: e for e in context.evidence}
    sources: set[SourceType] = set()

    for cit in hypothesis.supporting_evidence:
        record = context_map.get(cit.evidence_id)
        if record is not None:
            sources.add(record.source_type)

    count = len(sources)
    if count == 0:
        return 0.0
    if count == 1:
        return 0.5
    if count == 2:
        return 0.85
    return 1.0


def calculate_symptom_coverage(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Fraction of incident symptoms in context explained by supporting evidence.

    Returns float in [0.0, 1.0].
    """
    if not context.evidence:
        return 1.0 if hypothesis.supporting_evidence else 0.0

    symptom_evidence = [
        e for e in context.evidence if e.evidence_type in SYMPTOM_TYPES
    ]

    if not symptom_evidence:
        return 1.0 if hypothesis.supporting_evidence else 0.5

    symptom_ids = {e.evidence_id for e in symptom_evidence}
    supporting_ids = {c.evidence_id for c in hypothesis.supporting_evidence}

    covered = symptom_ids & supporting_ids
    return len(covered) / len(symptom_ids)


def calculate_temporal_consistency(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Evaluates whether cause precedes effect in the timeline.

    Returns:
    - 1.0 if change strictly preceded symptoms
    - 0.8 if non-change hypothesis or timestamps missing
    - 0.1 if change occurred after symptoms (causal violation)
    """
    if not context.evidence or not hypothesis.supporting_evidence:
        return 0.5

    context_map = {e.evidence_id: e for e in context.evidence}

    supporting_records = [
        context_map[c.evidence_id]
        for c in hypothesis.supporting_evidence
        if c.evidence_id in context_map
    ]

    change_times = [
        r.event_time for r in supporting_records
        if r.evidence_type in CHANGE_TYPES and r.event_time is not None
    ]
    symptom_times = [
        r.event_time for r in supporting_records
        if r.evidence_type in SYMPTOM_TYPES and r.event_time is not None
    ]
    if not symptom_times:
        symptom_times = [
            e.event_time for e in context.evidence
            if e.evidence_type in SYMPTOM_TYPES and e.event_time is not None
        ]

    if change_times and symptom_times:
        earliest_change = min(change_times)
        earliest_symptom = min(symptom_times)
        if earliest_change <= earliest_symptom:
            return 1.0
        return 0.1

    if hypothesis.root_cause_category not in CHANGE_RELATED_CATEGORIES:
        return 0.9

    return 0.7


def calculate_change_consistency(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Whether the cited change plausibly causes the failure.

    Returns float in [0.0, 1.0].
    """
    is_change_hypothesis = hypothesis.root_cause_category in CHANGE_RELATED_CATEGORIES

    if not context.evidence:
        return 0.5

    context_has_changes = any(
        e.evidence_type in CHANGE_TYPES for e in context.evidence
    )

    if is_change_hypothesis:
        if not context_has_changes:
            return 0.1

        context_map = {e.evidence_id: e for e in context.evidence}
        cites_change = any(
            context_map.get(c.evidence_id) is not None
            and context_map[c.evidence_id].evidence_type in CHANGE_TYPES
            for c in hypothesis.supporting_evidence
        )
        return 1.0 if cites_change else 0.4

    return 0.9


def calculate_specificity(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    How narrow, concrete, and testable the hypothesis is.

    Returns float in [0.0, 1.0].
    """
    score = 0.0

    component = (hypothesis.affected_component or "").strip().lower()
    if component and component not in {"unknown", "system", "general", "all"}:
        score += 0.3

    if hypothesis.root_cause_category != RootCauseCategory.UNKNOWN:
        score += 0.3

    statement = (hypothesis.statement or "").strip()
    if len(statement) >= 25:
        score += 0.2
    elif len(statement) >= 10:
        score += 0.1

    prediction = (hypothesis.testable_prediction or "").strip()
    if len(prediction) >= 15:
        score += 0.2
    elif len(prediction) > 0:
        score += 0.1

    return min(1.0, score)


def calculate_prediction_support(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Whether the testable prediction was supported by observations.

    Returns float in [0.0, 1.0].
    """
    prediction = (hypothesis.testable_prediction or "").strip().lower()
    if not prediction:
        return 0.0

    if not context.evidence:
        return 0.5

    # Check if words in prediction appear in evidence summaries
    pred_words = {w for w in prediction.split() if len(w) > 4}
    matches = 0
    for e in context.evidence:
        summary_lower = e.summary.lower()
        if any(w in summary_lower for w in pred_words):
            matches += 1

    if matches >= 2:
        return 1.0
    if matches == 1:
        return 0.8
    return 0.4


def calculate_contradiction_penalty(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Penalty factor for contradicting evidence.

    Returns:
    - 0.0 for 0 contradictions
    - 0.5 for 1 contradiction
    - 1.0 for 2 or more contradictions
    """
    count = len(hypothesis.contradicting_evidence)
    if count == 0:
        return 0.0
    if count == 1:
        return 0.5
    return 1.0


def calculate_missing_evidence_penalty(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Penalty factor for unresolved critical information gaps.

    Returns:
    - 0.0 for 0 unresolved needs
    - 0.5 for 1 unresolved need
    - 1.0 for 2 or more unresolved needs
    """
    count = len(hypothesis.missing_information_ids)
    if count == 0:
        return 0.0
    if count == 1:
        return 0.5
    return 1.0
