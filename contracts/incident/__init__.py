"""Incident contracts — alert ingestion and incident seed."""

from contracts.incident.alert import IncidentAlert
from contracts.incident.seed import IncidentSeed

__all__ = ["IncidentAlert", "IncidentSeed"]
