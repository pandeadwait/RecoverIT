"""IncidentAlert — the external input that triggers an investigation.

Validation rules (WORK_DIVISION §6.4):
- external_alert_id, service, environment, severity, detected_at,
  and message are required.
- detected_at must include timezone information (AwareDatetime).
- severity must be an allowed enumeration value.
- labels contain strings only.
- message and labels are untrusted data.
- Repeated external_alert_id values are idempotent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from contracts.enums import Severity


class IncidentAlert(BaseModel):
    """Incoming alert that creates or attaches to an incident."""

    schema_version: Literal["1.0"] = "1.0"
    external_alert_id: str
    service: str
    environment: str
    severity: Severity
    detected_at: AwareDatetime
    message: str
    labels: dict[str, str] = Field(default_factory=dict)
