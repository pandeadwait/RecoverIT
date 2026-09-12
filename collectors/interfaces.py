"""Abstract interfaces for operational data sources.

Per WORK_DIVISION §6.6 and §6.8, all operational data sources are accessed
through source-neutral interfaces returning typed results.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from contracts.collection.batch import QueryResult
from contracts.collection.capabilities import SourceCapability
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import SourceType
from contracts.incident.seed import IncidentSeed

# Aliases per specification
SourceQuery = EvidenceQuery
SourceResult = QueryResult


@runtime_checkable
class SourceRegistry(Protocol):
    """Protocol for discovering capabilities and looking up source adapters."""

    def capabilities(self, incident: IncidentSeed) -> Any:
        """Produce the SourceCapabilityCatalog for the given incident."""
        ...

    def get_source(self, source_type: SourceType) -> BaseSource | None:
        """Retrieve a registered source adapter by type."""
        ...


@runtime_checkable
class BaseSource(Protocol):
    """Base protocol that all source adapters implement."""

    source_type: SourceType

    def get_capability(
        self, incident: IncidentSeed | None = None
    ) -> SourceCapability:
        """Return the capability descriptor for this source."""
        ...

    def query(self, query: SourceQuery) -> SourceResult:
        """Execute a query against this source and return a typed result."""
        ...


@runtime_checkable
class LogSource(BaseSource, Protocol):
    """Abstract interface for log data sources."""

    source_type: SourceType = SourceType.LOGS


@runtime_checkable
class MetricSource(BaseSource, Protocol):
    """Abstract interface for metrics data sources."""

    source_type: SourceType = SourceType.METRICS


@runtime_checkable
class ChangeSource(BaseSource, Protocol):
    """Abstract interface for source code and Git change sources."""

    source_type: SourceType = SourceType.CHANGES


@runtime_checkable
class DeploymentSource(BaseSource, Protocol):
    """Abstract interface for deployment history sources."""

    source_type: SourceType = SourceType.DEPLOYMENTS


@runtime_checkable
class PipelineSource(BaseSource, Protocol):
    """Abstract interface for CI/CD pipeline sources."""

    source_type: SourceType = SourceType.PIPELINES


@runtime_checkable
class ConfigurationSource(BaseSource, Protocol):
    """Abstract interface for configuration change sources."""

    source_type: SourceType = SourceType.CONFIGURATION
