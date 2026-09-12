"""Alert ingestion service converting validated alerts into IncidentSeed records."""

from __future__ import annotations

from typing import Any, Protocol

from contracts.incident.alert import IncidentAlert
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import Clock, SystemClock
from ingestion.alert.id_gen import IdentifierGenerator, UUIDIdentifierGenerator
from ingestion.alert.repository import IncidentRepository, InMemoryIncidentRepository
from ingestion.validation.validator import validate_alert


class AlertIngestor(Protocol):
    """Abstract interface for alert ingestion per WORK_DIVISION §6.6."""

    def ingest(self, alert: IncidentAlert) -> IncidentSeed:
        """Ingest a validated IncidentAlert and produce an IncidentSeed."""
        ...


class DefaultAlertIngestor:
    """Default implementation of AlertIngestor.

    Owned responsibilities (WORK_DIVISION §6.2):
    - Alert ingestion and validation.
    - Incident deduplication key generation.
    - Service and environment scope extraction.
    - Construct IncidentSeed.
    - Idempotency on repeated external_alert_id.
    - Preserves untrusted messages and labels without interpretation.
    """

    def __init__(
        self,
        repository: IncidentRepository | None = None,
        clock: Clock | None = None,
        id_generator: IdentifierGenerator | None = None,
    ) -> None:
        self.repository = (
            repository if repository is not None else InMemoryIncidentRepository()
        )
        self.clock = clock if clock is not None else SystemClock()
        self.id_generator = (
            id_generator if id_generator is not None else UUIDIdentifierGenerator()
        )

    def ingest(self, alert: IncidentAlert) -> IncidentSeed:
        """Ingest an IncidentAlert.

        If an incident with the same external_alert_id has already been ingested,
        the existing IncidentSeed is returned idempotently.
        """
        existing = self.repository.get_by_external_alert_id(alert.external_alert_id)
        if existing is not None:
            return existing

        incident_id = self.id_generator.generate(prefix="inc")
        received_at = self.clock.now()

        seed = IncidentSeed(
            schema_version="1.0",
            incident_id=incident_id,
            external_alert_id=alert.external_alert_id,
            service=alert.service,
            environment=alert.environment,
            severity=alert.severity,
            detected_at=alert.detected_at,
            received_at=received_at,
            summary=alert.message,
            labels=dict(alert.labels),
        )

        self.repository.save(seed)
        return seed

    def ingest_raw(self, raw_alert: Any) -> IncidentSeed:
        """Validate raw input (dict, JSON string, or IncidentAlert) and ingest."""
        alert = validate_alert(raw_alert)
        return self.ingest(alert)
