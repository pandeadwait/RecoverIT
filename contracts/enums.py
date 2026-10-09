"""Canonical controlled values shared by every RecoverIT boundary.

The migration keeps compatibility aliases for the pre-LangGraph names, but all
new code imports enums from this module. Wire values are stable API contracts.
"""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceType(StrEnum):
    LOGS = "logs"
    METRICS = "metrics"
    CHANGES = "changes"
    DEPLOYMENTS = "deployments"
    PIPELINES = "pipelines"
    CONFIGURATION = "configuration"
    HEALTH = "health"


INVESTIGATION_SOURCE_TYPES: tuple[SourceType, ...] = tuple(SourceType)


class SourceStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"


class SourceCoverageStatus(StrEnum):
    NOT_QUERIED = "not_queried"
    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Reliability(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class EvidenceType(StrEnum):
    LOG_EVENT = "log_event"
    ERROR_EVENT = "error_event"
    WARNING_EVENT = "warning_event"
    INFO_EVENT = "info_event"
    METRIC_ANOMALY = "metric_anomaly"
    METRIC_NORMAL = "metric_normal"
    METRIC_OBSERVATION = "metric_observation"
    CODE_CHANGE = "code_change"
    SOURCE_CHANGE = "source_change"
    CONFIGURATION_CHANGE = "configuration_change"
    DEPLOYMENT_EVENT = "deployment_event"
    PIPELINE_RESULT = "pipeline_result"
    PIPELINE_EVENT = "pipeline_event"
    HEALTH_CHECK = "health_check"
    OPERATOR_NOTE = "operator_note"


class TimelineCategory(StrEnum):
    CHANGE = "change"
    DEPLOYMENT = "deployment"
    SYMPTOM = "symptom"
    ALERT = "alert"
    ACTION = "action"
    VERIFICATION = "verification"


class RelationshipType(StrEnum):
    PRECEDES = "PRECEDES"
    COINCIDES_WITH = "COINCIDES_WITH"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DEPLOYED_FROM = "DEPLOYED_FROM"
    AFFECTS = "AFFECTS"
    OBSERVED_ON = "OBSERVED_ON"
    PREDICTS = "PREDICTS"


class RelationshipCreator(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    OPERATOR = "operator"


class HypothesisStatus(StrEnum):
    ACTIVE = "active"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    SELECTED = "selected"


class ConfidenceLabel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RootCauseCategory(StrEnum):
    CONFIGURATION_REGRESSION = "configuration_regression"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPENDENCY_INCOMPATIBILITY = "dependency_incompatibility"
    DATABASE_OUTAGE = "database_outage"
    DEPLOYMENT_FAILURE = "deployment_failure"
    CODE_DEFECT = "code_defect"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    EXTERNAL_DEPENDENCY_FAILURE = "external_dependency_failure"
    UNKNOWN = "unknown"


class InformationPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InformationGapCategory(StrEnum):
    SYMPTOM_CONFIRMATION = "symptom_confirmation"
    TEMPORAL_CORRELATION = "temporal_correlation"
    DIRECT_CAUSAL_EVIDENCE = "direct_causal_evidence"
    CONTRADICTING_EVIDENCE = "contradicting_evidence"


class EvidenceRole(StrEnum):
    CAUSE = "cause"
    EFFECT = "effect"
    CORRELATION = "correlation"
    CONTRADICTION = "contradiction"
    CONTEXT = "context"


class InvestigationState(StrEnum):
    RECEIVED = "RECEIVED"
    ASSESSING_GAPS = "ASSESSING_GAPS"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    BUILDING_TIMELINE = "BUILDING_TIMELINE"
    GENERATING_HYPOTHESES = "GENERATING_HYPOTHESES"
    RANKING = "RANKING"
    COMPLETED = "COMPLETED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"


class InvestigationStatus(StrEnum):
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCES_UNAVAILABLE = "sources_unavailable"
    REPEATED_INVALID_OUTPUT = "repeated_invalid_output"
    CANCELLED = "cancelled"


class StopAction(StrEnum):
    CONTINUE = "continue"
    RANK = "rank"
    INCONCLUSIVE = "inconclusive"


class InformationValueLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Compatibility aliases used by pre-migration modules. They reference the
# canonical classes instead of defining alternate wire types.
SourceCoverage = SourceCoverageStatus
SourceCoverageState = SourceCoverageStatus
InformationValue = InformationValueLevel


__all__ = [
    "ConfidenceLabel",
    "EvidenceRole",
    "EvidenceType",
    "HypothesisStatus",
    "InformationGapCategory",
    "InformationPriority",
    "InformationValue",
    "InformationValueLevel",
    "INVESTIGATION_SOURCE_TYPES",
    "InvestigationState",
    "InvestigationStatus",
    "RelationshipCreator",
    "RelationshipType",
    "Reliability",
    "RootCauseCategory",
    "Severity",
    "SourceCoverage",
    "SourceCoverageState",
    "SourceCoverageStatus",
    "SourceStatus",
    "SourceType",
    "StopAction",
    "StopReason",
    "TimelineCategory",
]
