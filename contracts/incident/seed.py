"""IncidentSeed — validated incident record produced from an alert.

This is the primary output of Person 1's alert-ingestion step and
the starting input for both Person 2 (normalization) and Person 3
(investigation).  See WORK_DIVISION §6.5.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from contracts.common import BoundaryModel
from contracts.enums import Severity


class IncidentSeed(BoundaryModel):
    """Validated incident seed created from an IncidentAlert."""

    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    external_alert_id: str
    service: str
    environment: str
    severity: Severity
    detected_at: AwareDatetime
    received_at: AwareDatetime
    summary: str
    labels: dict[str, str] = Field(default_factory=dict)
