"""
Incident contracts.

Defines IncidentAlert (external input) and IncidentSeed (validated internal
representation). Person 1 owns implementation; Person 3 consumes IncidentSeed.

See WORK_DIVISION.md §6.4 and §6.5.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from contracts.common import ContractModel, Severity


class IncidentAlert(ContractModel):
    """
    External alert input that triggers an investigation.

    Validation rules (from WORK_DIVISION.md §6.4):
    - external_alert_id, service, environment, severity, detected_at,
      and message are required.
    - detected_at must include timezone information.
    - severity must be an allowed enumeration.
    - labels contain strings only.
    - Message and labels are untrusted data.
    - Repeated external_alert_id values are idempotent.
    """
    external_alert_id: str = Field(
        ...,
        description="Stable external alert ID for idempotent ingestion.",
    )
    service: str = Field(
        ...,
        description="Affected service name.",
    )
    environment: str = Field(
        ...,
        description="Environment where the alert originated.",
    )
    severity: Severity = Field(
        ...,
        description="Alert severity level.",
    )
    detected_at: datetime = Field(
        ...,
        description="When the alerting system detected the issue (with tz).",
    )
    message: str = Field(
        ...,
        description="Human-readable alert message. Treated as untrusted data.",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary string labels. Treated as untrusted data.",
    )


class IncidentSeed(ContractModel):
    """
    Validated incident created from an IncidentAlert by Person 1.

    This is the canonical incident identity consumed by Person 2 and Person 3.

    See WORK_DIVISION.md §6.5.
    """
    incident_id: str = Field(
        ...,
        description="Globally unique incident identifier.",
    )
    external_alert_id: str = Field(
        ...,
        description="Original external alert ID.",
    )
    service: str = Field(
        ...,
        description="Affected service name.",
    )
    environment: str = Field(
        ...,
        description="Environment where the alert originated.",
    )
    severity: Severity = Field(
        ...,
        description="Validated severity level.",
    )
    detected_at: datetime = Field(
        ...,
        description="When the issue was detected (with tz).",
    )
    received_at: datetime = Field(
        ...,
        description="When the alert was received by this system (with tz).",
    )
    summary: str = Field(
        ...,
        description="Short summary derived from the alert message.",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary string labels from the original alert.",
    )
