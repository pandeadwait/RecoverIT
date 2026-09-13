"""Common structured error format (WORK_DIVISION §5.1).

All source and system errors use this envelope so consumers can
handle failures uniformly without catching vendor-specific exceptions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StructuredError(BaseModel):
    """Source-neutral error returned by adapters and services."""

    schema_version: Literal["1.0"] = "1.0"
    code: str
    message: str
    retryable: bool = False
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
