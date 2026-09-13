"""Shared validation, immutability, and deterministic serialization helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
import json
from types import MappingProxyType
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"
JSONValue = str | int | float | bool | None | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
T = TypeVar("T")


class ContractValidationError(ValueError):
    """Raised when data violates a portable contract at a boundary."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


class SourceType(StrEnum):
    LOGS = "logs"
    METRICS = "metrics"
    CHANGES = "changes"
    DEPLOYMENTS = "deployments"
    PIPELINES = "pipelines"
    CONFIGURATION = "configuration"
    HEALTH = "health"
    OPERATOR = "operator"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"
    PARTIAL = "partial"


class Reliability(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class SourceCoverageState(StrEnum):
    AVAILABLE = "available"
    EMPTY = "empty"
    NOT_QUERIED = "not_queried"
    UNAVAILABLE = "unavailable"


# Person 3's schema name for the same wire-level concept.
SourceCoverageStatus = SourceCoverageState


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


class StopReason(StrEnum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCES_UNAVAILABLE = "sources_unavailable"
    REPEATED_INVALID_OUTPUT = "repeated_invalid_output"


class InformationValueLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BoundaryModel(BaseModel):
    """Strict Pydantic boundary model with canonical UTC datetimes."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _require_utc_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamp must include timezone information")
            return value.astimezone(timezone.utc)
        return value


class ContractModel(BoundaryModel):
    """Frozen Pydantic base used by Person 3's serialized schemas."""

    model_config = ConfigDict(
        frozen=True,
        use_enum_values=True,
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    schema_version: Literal["1.0"] = Field(default=SCHEMA_VERSION)


def require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("invalid_type", path, "must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ContractValidationError("invalid_key", path, "must contain string keys")
    return value


def reject_unknown_fields(
    data: Mapping[str, Any], allowed: set[str], path: str
) -> None:
    """Reject unversioned extension fields at a serialized trust boundary."""

    unknown = set(data) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ContractValidationError(
            "unknown_field", path, f"unexpected field(s): {names}"
        )


def require_list(value: object, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ContractValidationError("invalid_type", path, "must be an array")
    return value


def require_string(value: object, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractValidationError("invalid_type", path, "must be a string")
    if not allow_empty and not value.strip():
        raise ContractValidationError("required", path, "must not be blank")
    return value


def require_identifier(value: object, path: str) -> str:
    identifier = require_string(value, path)
    if len(identifier) > 256:
        raise ContractValidationError("invalid_identifier", path, "must be at most 256 characters")
    return identifier


def require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError("invalid_type", path, "must be a boolean")
    return value


def require_int(value: object, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError("invalid_type", path, "must be an integer")
    if minimum is not None and value < minimum:
        raise ContractValidationError("out_of_range", path, f"must be at least {minimum}")
    return value


def require_schema_version(data: Mapping[str, Any], path: str = "schema_version") -> str:
    version = require_string(data.get("schema_version"), path)
    if version != SCHEMA_VERSION:
        raise ContractValidationError(
            "unsupported_schema_version", path, f"expected {SCHEMA_VERSION!r}, got {version!r}"
        )
    return version


def parse_datetime(value: object, path: str) -> datetime:
    text = require_string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractValidationError("invalid_timestamp", path, "must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError("timezone_required", path, "must include timezone information")
    return parsed.astimezone(timezone.utc)


def datetime_to_wire(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("contract datetimes must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_extensible_code(value: object, path: str, known: set[str]) -> str:
    code = require_string(value, path)
    if code in known or code == "unknown" or code.startswith("x-"):
        return code
    allowed = ", ".join(sorted(known))
    raise ContractValidationError(
        "unsupported_enum", path, f"must be one of {allowed}, 'unknown', or an 'x-' extension"
    )


def freeze_json(value: object, path: str = "value") -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ContractValidationError("invalid_json", path, "must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ContractValidationError("invalid_key", path, "must contain string keys")
            frozen[key] = freeze_json(nested, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json(item, f"{path}[{index}]") for index, item in enumerate(value))
    raise ContractValidationError("invalid_json", path, "must contain JSON-compatible values")


def thaw_json(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Return the stable JSON representation used by hashing and deterministic IDs."""
    return json.dumps(to_wire_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def to_wire_value(value: object) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, datetime):
        return datetime_to_wire(value)
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_wire_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [to_wire_value(item) for item in value]
    if isinstance(value, list):
        return [to_wire_value(item) for item in value]
    return value


def require_optional_datetime(data: Mapping[str, Any], key: str, path: str) -> datetime | None:
    value = data.get(key)
    return None if value is None else parse_datetime(value, path)
