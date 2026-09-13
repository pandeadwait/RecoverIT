"""Common structured error format (WORK_DIVISION §5.1).

All source and system errors use this envelope so consumers can
handle failures uniformly without catching vendor-specific exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from contracts.common import (
    BoundaryModel,
    ContractValidationError,
    SCHEMA_VERSION,
    freeze_json,
    require_identifier,
    require_mapping,
    reject_unknown_fields,
    require_schema_version,
    require_string,
    thaw_json,
)


class StructuredError(BoundaryModel):
    """Source-neutral error returned by adapters and services."""

    schema_version: Literal["1.0"] = "1.0"
    code: str
    message: str
    retryable: bool = False
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessingWarning:
    schema_version: str
    code: str
    message: str
    path: str | None = None
    record_id: str | None = None
    details: Any | None = None

    def __post_init__(self) -> None:
        require_string(self.schema_version, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError("unsupported_schema_version", "schema_version", "must be '1.0'")
        require_string(self.code, "code")
        require_string(self.message, "message")
        if self.path is not None:
            require_string(self.path, "path")
        if self.record_id is not None:
            require_identifier(self.record_id, "record_id")
        if self.details is not None:
            object.__setattr__(self, "details", freeze_json(self.details, "details"))

    @classmethod
    def from_dict(cls, value: object) -> "ProcessingWarning":
        if isinstance(value, str):
            return cls(
                schema_version=SCHEMA_VERSION,
                code="x-collector-warning",
                message=require_string(value, "processing_warning"),
            )
        data = require_mapping(value, "processing_warning")
        reject_unknown_fields(
            data,
            {"schema_version", "code", "message", "path", "record_id", "details"},
            "processing_warning",
        )
        require_schema_version(data)
        return cls(
            schema_version=data["schema_version"],
            code=data.get("code"),
            message=data.get("message"),
            path=data.get("path"),
            record_id=data.get("record_id"),
            details=data.get("details"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema_version": self.schema_version, "code": self.code, "message": self.message}
        if self.path is not None:
            result["path"] = self.path
        if self.record_id is not None:
            result["record_id"] = self.record_id
        if self.details is not None:
            result["details"] = thaw_json(self.details)
        return result


@dataclass(frozen=True, slots=True)
class ProcessingError:
    schema_version: str
    code: str
    message: str
    retryable: bool
    source: str | None = None
    path: str | None = None
    record_id: str | None = None
    details: Any | None = None

    def __post_init__(self) -> None:
        require_string(self.schema_version, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError("unsupported_schema_version", "schema_version", "must be '1.0'")
        require_string(self.code, "code")
        require_string(self.message, "message")
        if not isinstance(self.retryable, bool):
            raise ContractValidationError("invalid_type", "retryable", "must be a boolean")
        if self.source is not None:
            require_string(self.source, "source")
        if self.path is not None:
            require_string(self.path, "path")
        if self.record_id is not None:
            require_identifier(self.record_id, "record_id")
        if self.details is not None:
            object.__setattr__(self, "details", freeze_json(self.details, "details"))

    @classmethod
    def from_dict(cls, value: object) -> "ProcessingError":
        data = require_mapping(value, "processing_error")
        reject_unknown_fields(
            data,
            {"schema_version", "code", "message", "retryable", "source", "path", "record_id", "details"},
            "processing_error",
        )
        require_schema_version(data)
        return cls(
            schema_version=data["schema_version"],
            code=data.get("code"),
            message=data.get("message"),
            retryable=data.get("retryable"),
            source=data.get("source"),
            path=data.get("path"),
            record_id=data.get("record_id"),
            details=data.get("details"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.source is not None:
            result["source"] = self.source
        if self.path is not None:
            result["path"] = self.path
        if self.record_id is not None:
            result["record_id"] = self.record_id
        if self.details is not None:
            result["details"] = thaw_json(self.details)
        return result


__all__ = [
    "ContractValidationError",
    "ProcessingError",
    "ProcessingWarning",
    "StructuredError",
]
