"""Incident repository abstraction and in-memory implementation."""

from __future__ import annotations

from typing import Protocol

from contracts.incident.seed import IncidentSeed


class IncidentRepository(Protocol):
    """Abstract repository for persisting and retrieving IncidentSeed records."""

    def save(self, incident: IncidentSeed) -> None:
        """Persist an IncidentSeed."""
        ...

    def get(self, incident_id: str) -> IncidentSeed | None:
        """Retrieve an IncidentSeed by its incident_id."""
        ...

    def get_by_external_alert_id(self, external_alert_id: str) -> IncidentSeed | None:
        """Retrieve an IncidentSeed by its external_alert_id for deduplication."""
        ...

    def list_all(self) -> list[IncidentSeed]:
        """List all stored IncidentSeeds."""
        ...


class InMemoryIncidentRepository:
    """In-memory storage for IncidentSeeds, supporting idempotency lookups."""

    def __init__(self) -> None:
        self._by_id: dict[str, IncidentSeed] = {}
        self._by_external_id: dict[str, str] = {}

    def save(self, incident: IncidentSeed) -> None:
        self._by_id[incident.incident_id] = incident
        self._by_external_id[incident.external_alert_id] = incident.incident_id

    def get(self, incident_id: str) -> IncidentSeed | None:
        return self._by_id.get(incident_id)

    def get_by_external_alert_id(self, external_alert_id: str) -> IncidentSeed | None:
        incident_id = self._by_external_id.get(external_alert_id)
        if incident_id is not None:
            return self._by_id.get(incident_id)
        return None

    def list_all(self) -> list[IncidentSeed]:
        return list(self._by_id.values())
