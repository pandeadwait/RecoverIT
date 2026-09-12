"""
Structured error schemas.

All components return StructuredError instead of unhandled exceptions
at trust boundaries. See WORK_DIVISION.md §5.1.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from contracts.common import ContractModel


class StructuredError(ContractModel):
    """
    Common error format returned by any component at a trust boundary.

    Example:
        {
            "schema_version": "1.0",
            "code": "SOURCE_UNAVAILABLE",
            "message": "The metrics source did not respond before the deadline.",
            "retryable": true,
            "source": "metrics",
            "details": {"query_id": "qry_123"}
        }
    """
    code: str = Field(
        ...,
        description="Machine-readable error code, e.g. SOURCE_UNAVAILABLE.",
    )
    message: str = Field(
        ...,
        description="Human-readable description of the error.",
    )
    retryable: bool = Field(
        default=False,
        description="Whether the operation may be retried.",
    )
    source: str | None = Field(
        default=None,
        description="Component or source that originated the error.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional machine-readable context.",
    )


# ---------------------------------------------------------------------------
# Common error codes
# ---------------------------------------------------------------------------

# These are string constants so consumers can match without importing enums.

SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
INVALID_QUERY = "INVALID_QUERY"
INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
CITATION_INVALID = "CITATION_INVALID"
REASONING_PROVIDER_ERROR = "REASONING_PROVIDER_ERROR"
DUPLICATE_QUERY = "DUPLICATE_QUERY"
CROSS_INCIDENT_REFERENCE = "CROSS_INCIDENT_REFERENCE"
