"""EvidenceQueryPlan — planned queries for evidence collection.

Produced by Person 3, consumed by Person 1.  Each query targets a
specific source type with validated parameters and an expected
information value.  See WORK_DIVISION §8.5.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from contracts.enums import InformationValue, SourceType


class EvidenceQuery(BaseModel):
    """A single evidence query within a plan."""

    query_id: str
    source_type: SourceType
    question: str
    parameters: dict[str, Any]
    related_information_ids: list[str] = Field(default_factory=list)
    discriminates_hypothesis_ids: list[str] = Field(default_factory=list)
    expected_information_value: InformationValue


class EvidenceQueryPlan(BaseModel):
    """Collection of queries to execute in one investigation round.

    The plan can contain zero queries only when stop_reason is present.
    """

    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    plan_id: str
    round: int
    queries: list[EvidenceQuery]
    stop_reason: str | None = None
