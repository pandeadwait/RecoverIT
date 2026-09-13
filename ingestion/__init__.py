"""Ingestion package — boundary layer between investigation and operational sources."""

from ingestion.alert import (
    AlertIngestor,
    Clock,
    DefaultAlertIngestor,
    DeterministicIdentifierGenerator,
    FrozenClock,
    IdentifierGenerator,
    InMemoryIncidentRepository,
    IncidentRepository,
    SystemClock,
    UUIDIdentifierGenerator,
)
from ingestion.capabilities import (
    DEFAULT_CAPABILITY_SPECS,
    DefaultSourceRegistry,
    SourceRegistry,
    get_default_capability,
)
from ingestion.validation import AlertValidationError, validate_alert

__all__ = [
    "AlertIngestor",
    "DefaultAlertIngestor",
    "AlertValidationError",
    "validate_alert",
    "SourceRegistry",
    "DefaultSourceRegistry",
    "DEFAULT_CAPABILITY_SPECS",
    "get_default_capability",
    "Clock",
    "SystemClock",
    "FrozenClock",
    "IdentifierGenerator",
    "UUIDIdentifierGenerator",
    "DeterministicIdentifierGenerator",
    "IncidentRepository",
    "InMemoryIncidentRepository",
]
