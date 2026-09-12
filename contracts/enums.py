"""Shared enumerations used across all contracts.

Every controlled field uses an enumeration so unknown values are
rejected at trust boundaries (WORK_DIVISION §5).
"""

from enum import Enum


class Severity(str, Enum):
    """Alert / incident severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceType(str, Enum):
    """The six evidence-source categories the system can query."""

    LOGS = "logs"
    METRICS = "metrics"
    CHANGES = "changes"
    DEPLOYMENTS = "deployments"
    PIPELINES = "pipelines"
    CONFIGURATION = "configuration"


class SourceStatus(str, Enum):
    """Outcome status of a single source query."""

    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"


class SourceCoverage(str, Enum):
    """Per-source coverage state inside an IncidentContextSnapshot."""

    AVAILABLE = "available"
    NOT_QUERIED = "not_queried"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class InformationValue(str, Enum):
    """Expected discriminating value of an evidence query."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
