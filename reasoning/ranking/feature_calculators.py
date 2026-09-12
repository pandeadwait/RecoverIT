"""
Deterministic feature calculators for hypothesis evidence scoring.

All calculators are pure functions that take a Hypothesis and an
IncidentContextSnapshot and return a deterministic numeric score.
No calculator invokes external LLMs or infrastructure.

See WORK_DIVISION.md §8.11 and ARCHITECTURE.md §8.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from contracts.common import (
    EvidenceType,
    RootCauseCategory,
    SourceType,
    TimelineCategory,
)
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import Hypothesis

CHANGE_CATEGORIES = frozenset({
    RootCauseCategory.CONFIGURATION_REGRESSION,
    RootCauseCategory.DEPLOYMENT_FAILURE,
    RootCauseCategory.CODE_DEFECT,
})


def calculate_independent_source_support(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Count distinct SourceTypes represented in supporting evidence.
    Returns a score normalized between 0.0 and 1.0.
    """
    if not hypothesis.supporting_evidence:
        return 0.0

    context_map = {e.evidence_id: e for e in context.evidence}
    sources: set[SourceType] = set()

    for cit in hypothesis.supporting_evidence:
        rec = context_map.get(cit.evidence_id)
        if rec is not None:
            sources.add(rec.source_type)

    if not sources:
        # Fallback if supporting evidence is cited by ID but snapshot only has projections
        return min(1.0, len(hypothesis.supporting_evidence) / 3.0)

    # 3 or more independent sources gives 100% (1.0)
    return min(1.0, len(sources) / 3.0)


def calculate_symptom_coverage(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Fraction of incident symptoms/errors explained by this hypothesis.
    Returns a score between 0.0 and 1.0.
    """
    symptoms = [
        e for e in context.evidence
        if e.evidence_type in {EvidenceType.ERROR_EVENT, EvidenceType.WARNING_EVENT, EvidenceType.METRIC_ANOMALY}
    ]

    if not symptoms:
        # If no explicit symptom evidence in snapshot, default to 1.0 if supporting evidence exists
        return 1.0 if hypothesis.supporting_evidence else 0.5

    symptom_ids = {e.evidence_id for e in symptoms}
    supporting_ids = {c.evidence_id for c in hypothesis.supporting_evidence}

    covered = symptom_ids & supporting_ids
    return len(covered) / len(symptom_ids)


def calculate_temporal_consistency(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Check if changes/causes occurred prior to or coincident with symptoms.
    Returns a score between 0.0 and 1.0.
    """
    if not context.timeline:
        return 0.75

    change_events = [
        t for t in context.timeline
        if t.category in {TimelineCategory.CHANGE, TimelineCategory.DEPLOYMENT}
    ]
    symptom_events = [
        t for t in context.timeline
        if t.category in {TimelineCategory.SYMPTOM, TimelineCategory.ALERT}
    ]

    if not change_events or not symptom_events:
        return 0.75

    earliest_change = min(t.event_time for t in change_events)
    earliest_symptom = min(t.event_time for t in symptom_events)

    if earliest_change <= earliest_symptom:
        return 1.0
    return 0.0  # Symptom happened before change -> temporally inconsistent


def calculate_change_consistency(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Check whether cited change plausibly matches the hypothesis category and component.
    Returns a score between 0.0 and 1.0.
    """
    if hypothesis.root_cause_category in CHANGE_CATEGORIES:
        # Needs change/deployment evidence matching component
        has_change_evidence = any(
            e.source_type in {SourceType.DEPLOYMENTS, SourceType.CHANGES}
            for e in context.evidence
        )
        if has_change_evidence and hypothesis.supporting_evidence:
            return 1.0
        elif has_change_evidence:
            return 0.6
        return 0.2
    else:
        # Non-change hypothesis (DB outage, external dependency, resource exhaustion)
        if hypothesis.supporting_evidence:
            return 1.0
        return 0.75


def calculate_specificity(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Evaluate how narrow, specific, and testable the hypothesis is.
    Returns a score between 0.0 and 1.0.
    """
    score = 0.0

    if hypothesis.statement and len(hypothesis.statement) > 15:
        score += 0.25
    if hypothesis.affected_component and hypothesis.affected_component != "unknown":
        score += 0.25
    if hypothesis.root_cause_category != RootCauseCategory.UNKNOWN:
        score += 0.25
    if hypothesis.testable_prediction and len(hypothesis.testable_prediction) > 10:
        score += 0.25

    return min(1.0, score)


def calculate_prediction_support(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Check whether the testable prediction is supported by evidence.
    Returns a score between 0.0 and 1.0.
    """
    if not hypothesis.testable_prediction:
        return 0.0

    # If hypothesis has active status and supporting evidence, prediction has support
    if hypothesis.supporting_evidence:
        return 1.0
    return 0.5


def calculate_contradiction_penalty(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Calculate penalty from contradicting evidence.
    Returns a penalty score between 0.0 and 1.0.
    """
    if not hypothesis.contradicting_evidence:
        return 0.0

    count = len(hypothesis.contradicting_evidence)
    return min(1.0, count / 2.0)


def calculate_missing_evidence_penalty(
    hypothesis: Hypothesis,
    context: IncidentContextSnapshot,
) -> float:
    """
    Calculate penalty from unresolved critical information needs.
    Returns a penalty score between 0.0 and 1.0.
    """
    if not hypothesis.missing_information_ids:
        return 0.0

    count = len(hypothesis.missing_information_ids)
    return min(1.0, count / 3.0)
