"""Portable error and warning contracts for investigation processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.common import ContractValidationError, SCHEMA_VERSION, freeze_json, require_extensible_code, require_identifier, require_mapping, require_schema_version, require_string, thaw_json


_ISSUE_SEVERITIES = {"warning", "error"}


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
        require_extensible_code(self.code, "code", {"malformed_record", "partial_result", "unknown_identity", "time_uncertain", "redacted"})
        require_string(self.message, "message")
        if self.path is not None:
            require_string(self.path, "path")
        if self.record_id is not None:
            require_identifier(self.record_id, "record_id")
        if self.details is not None:
            object.__setattr__(self, "details", freeze_json(self.details, "details"))

    @classmethod
    def from_dict(cls, value: object) -> "ProcessingWarning":
        data = require_mapping(value, "processing_warning")
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
    path: str | None = None
    record_id: str | None = None
    details: Any | None = None

    def __post_init__(self) -> None:
        require_string(self.schema_version, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError("unsupported_schema_version", "schema_version", "must be '1.0'")
        require_extensible_code(self.code, "code", {"invalid_envelope", "incident_mismatch", "storage_failure", "policy_rejected", "source_failure"})
        require_string(self.message, "message")
        if not isinstance(self.retryable, bool):
            raise ContractValidationError("invalid_type", "retryable", "must be a boolean")
        if self.path is not None:
            require_string(self.path, "path")
        if self.record_id is not None:
            require_identifier(self.record_id, "record_id")
        if self.details is not None:
            object.__setattr__(self, "details", freeze_json(self.details, "details"))

    @classmethod
    def from_dict(cls, value: object) -> "ProcessingError":
        data = require_mapping(value, "processing_error")
        require_schema_version(data)
        return cls(
            schema_version=data["schema_version"],
            code=data.get("code"),
            message=data.get("message"),
            retryable=data.get("retryable"),
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
        if self.path is not None:
            result["path"] = self.path
        if self.record_id is not None:
            result["record_id"] = self.record_id
        if self.details is not None:
            result["details"] = thaw_json(self.details)
        return result
