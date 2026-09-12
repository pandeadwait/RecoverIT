"""
Shared enumerations, base types, and constants used across all contracts.

All contracts derive from ContractModel which enforces schema_version,
JSON serialization, and deterministic canonical output.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Shared Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Incident severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceType(str, Enum):
    """Categories of operational data sources."""
    LOGS = "logs"
    METRICS = "metrics"
    CHANGES = "changes"
    DEPLOYMENTS = "deployments"
    PIPELINES = "pipelines"
    CONFIGURATION = "configuration"
    HEALTH = "health"
    OPERATOR = "operator"


class SourceStatus(str, Enum):
    """Outcome status of a source query."""
    OK = "ok"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"
    PARTIAL = "partial"


class Reliability(str, Enum):
    """Trustworthiness assessment of an evidence record."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceCoverageStatus(str, Enum):
    """Whether a source category has been queried and returned data."""
    AVAILABLE = "available"
    NOT_QUERIED = "not_queried"
    UNAVAILABLE = "unavailable"
    EMPTY = "empty"


class EvidenceType(str, Enum):
    """Classification of normalized evidence records."""
    ERROR_EVENT = "error_event"
    WARNING_EVENT = "warning_event"
    INFO_EVENT = "info_event"
    METRIC_ANOMALY = "metric_anomaly"
    METRIC_NORMAL = "metric_normal"
    CODE_CHANGE = "code_change"
    CONFIGURATION_CHANGE = "configuration_change"
    DEPLOYMENT_EVENT = "deployment_event"
    PIPELINE_RESULT = "pipeline_result"
    HEALTH_CHECK = "health_check"
    OPERATOR_NOTE = "operator_note"


class TimelineCategory(str, Enum):
    """Classification of timeline events."""
    CHANGE = "change"
    DEPLOYMENT = "deployment"
    SYMPTOM = "symptom"
    ALERT = "alert"
    ACTION = "action"
    VERIFICATION = "verification"


class RelationshipType(str, Enum):
    """Types of temporal and causal relationships between events."""
    PRECEDES = "PRECEDES"
    COINCIDES_WITH = "COINCIDES_WITH"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DEPLOYED_FROM = "DEPLOYED_FROM"
    AFFECTS = "AFFECTS"
    OBSERVED_ON = "OBSERVED_ON"
    PREDICTS = "PREDICTS"


class RelationshipCreator(str, Enum):
    """Who/what created a relationship."""
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    OPERATOR = "operator"


class HypothesisStatus(str, Enum):
    """Lifecycle status of a root-cause hypothesis."""
    ACTIVE = "active"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    SELECTED = "selected"


class ConfidenceLabel(str, Enum):
    """Human-readable confidence band for evidence scores."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RootCauseCategory(str, Enum):
    """Broad categories for root-cause classification."""
    CONFIGURATION_REGRESSION = "configuration_regression"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPENDENCY_INCOMPATIBILITY = "dependency_incompatibility"
    DATABASE_OUTAGE = "database_outage"
    DEPLOYMENT_FAILURE = "deployment_failure"
    CODE_DEFECT = "code_defect"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    EXTERNAL_DEPENDENCY_FAILURE = "external_dependency_failure"
    UNKNOWN = "unknown"


class InformationPriority(str, Enum):
    """Priority of a missing-information item."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InvestigationStatus(str, Enum):
    """Terminal status of a ranked hypothesis set."""
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"


class StopReason(str, Enum):
    """Why an investigation stopped without a confident conclusion."""
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCES_UNAVAILABLE = "sources_unavailable"
    REPEATED_INVALID_OUTPUT = "repeated_invalid_output"


class InformationValueLevel(str, Enum):
    """Expected information gain from a planned query."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Base Model
# ---------------------------------------------------------------------------


class ContractModel(BaseModel):
    """
    Base for all contract objects.

    Enforces:
    - schema_version on every instance
    - JSON-serializable output
    - Frozen (immutable) instances for determinism
    """
    model_config = ConfigDict(
        frozen=True,
        use_enum_values=True,
        json_schema_extra={"additionalProperties": False},
    )

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Version of the schema this object conforms to.",
    )


# ---------------------------------------------------------------------------
# Shared small types
# ---------------------------------------------------------------------------


class Labels(BaseModel):
    """Arbitrary string labels attached to alerts and incidents."""
    model_config = ConfigDict(frozen=True)

    # Allow any string key → string value via model_extra
    # We use a simple dict field instead.
    pass


# We represent labels as dict[str, str] directly in consuming models
# rather than a separate class, for simplicity and JSON fidelity.
