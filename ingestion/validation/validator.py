"""Validation logic for raw incoming alert payloads."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from contracts.errors import StructuredError
from contracts.incident.alert import IncidentAlert


class AlertValidationError(Exception):
    """Raised when an alert payload fails schema validation."""

    def __init__(self, message: str, error_detail: StructuredError) -> None:
        super().__init__(message)
        self.error = error_detail


def validate_alert(raw_data: Any) -> IncidentAlert:
    """Validate raw data (dict, JSON string, or IncidentAlert) against the IncidentAlert schema.

    Returns:
        IncidentAlert: The validated IncidentAlert object.

    Raises:
        AlertValidationError: If validation fails, containing a StructuredError payload.
    """
    if isinstance(raw_data, IncidentAlert):
        return raw_data

    try:
        if isinstance(raw_data, str):
            return IncidentAlert.model_validate_json(raw_data)
        elif isinstance(raw_data, dict):
            return IncidentAlert.model_validate(raw_data)
        else:
            type_name = type(raw_data).__name__
            structured_err = StructuredError(
                code="INVALID_ALERT_TYPE",
                message=f"Alert input must be dict, JSON string, or IncidentAlert, got {type_name}.",
                retryable=False,
                source="alert_validation",
                details={"input_type": type_name},
            )
            raise AlertValidationError(structured_err.message, structured_err)
    except ValidationError as e:
        error_details = []
        for err in e.errors():
            loc = ".".join(str(item) for item in err.get("loc", []))
            msg = err.get("msg", "unknown error")
            error_details.append(f"{loc}: {msg}" if loc else msg)
        combined_msg = f"Alert validation failed: {'; '.join(error_details)}"
        structured_err = StructuredError(
            code="INVALID_ALERT_PAYLOAD",
            message=combined_msg,
            retryable=False,
            source="alert_validation",
            details={"validation_errors": e.errors()},
        )
        raise AlertValidationError(combined_msg, structured_err) from e
