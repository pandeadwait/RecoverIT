"""EvidenceQueryPlan — planned queries for evidence collection.

Produced by Person 3, consumed by Person 1.  Each query targets a
specific source type with validated parameters and an expected
information value.  See WORK_DIVISION §8.5.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from contracts.common import BoundaryModel
from contracts.enums import InformationValue, SourceType


class EvidenceQuery(BoundaryModel):
    """A single evidence query within a plan."""

    query_id: str
    source_type: SourceType
    question: str
    parameters: dict[str, Any]
    related_information_ids: list[str] = Field(default_factory=list)
    discriminates_hypothesis_ids: list[str] = Field(default_factory=list)
    expected_information_value: InformationValue


class EvidenceQueryPlan(BoundaryModel):
    """Collection of queries to execute in one investigation round.

    The plan can contain zero queries only when stop_reason is present.
    """

    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    plan_id: str
    round: int
    queries: list[EvidenceQuery]
    stop_reason: str | None = None

    @model_validator(mode="after")
    def require_queries_or_stop_reason(self) -> "EvidenceQueryPlan":
        if not self.queries and self.stop_reason is None:
            raise ValueError("an empty query plan requires stop_reason")
        if self.queries and self.stop_reason is not None:
            raise ValueError("stop_reason requires an empty query list")
        return self
