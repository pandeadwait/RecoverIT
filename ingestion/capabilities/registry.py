"""Source capability discovery and registry.

Implements SourceRegistry per WORK_DIVISION §6.6 and §6.8, dynamically producing
the SourceCapabilityCatalog for an incident.
"""

from __future__ import annotations

import logging
from typing import Protocol

from collectors.interfaces import BaseSource
from collectors.specs import DEFAULT_CAPABILITY_SPECS, get_default_capability
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.enums import SourceType
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


class SourceRegistry(Protocol):
    """Abstract interface for discovering source capabilities."""

    def capabilities(self, incident: IncidentSeed) -> SourceCapabilityCatalog:
        """Produce the SourceCapabilityCatalog for the given incident."""
        ...


class DefaultSourceRegistry:
    """Default registry managing source adapters and building capability catalogs.

    Owned responsibilities (WORK_DIVISION §6.2, §6.6):
    - Maintain server-owned registry of source adapters.
    - Query registered adapters dynamically or use registered capabilities.
    - Guarantee all six source categories are represented in catalog.
    - Handle unreachable/failing sources gracefully (mark available=False).
    - Emit vendor-neutral SourceCapabilityCatalog validated against schema.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock if clock is not None else SystemClock()
        self._sources: dict[SourceType, BaseSource] = {}
        self._capabilities: dict[SourceType, SourceCapability] = {}

    def register_source(self, source: BaseSource) -> None:
        """Register an active source adapter."""
        self._sources[source.source_type] = source

    def unregister_source(self, source_type: SourceType) -> None:
        """Remove a registered source adapter."""
        self._sources.pop(source_type, None)

    def get_source(self, source_type: SourceType) -> BaseSource | None:
        """Retrieve a registered source adapter by type."""
        return self._sources.get(source_type)

    def register_capability(self, capability: SourceCapability) -> None:
        """Register an explicit SourceCapability override for a source type."""
        self._capabilities[capability.source_type] = capability

    def unregister_capability(self, source_type: SourceType) -> None:
        """Remove an explicit capability override."""
        self._capabilities.pop(source_type, None)

    def capabilities(self, incident: IncidentSeed) -> SourceCapabilityCatalog:
        """Generate a complete SourceCapabilityCatalog for the incident.

        All six source categories are always present. Unavailable or failing
        sources are marked available=False rather than omitted or crashing.
        """
        sources_list: list[SourceCapability] = []

        # Order iteration consistently across all enum values
        for source_type in SourceType:
            # 1. Explicit capability override if set
            if source_type in self._capabilities:
                sources_list.append(self._capabilities[source_type])
                continue

            # 2. Registered source adapter
            adapter = self._sources.get(source_type)
            if adapter is not None:
                try:
                    cap = adapter.get_capability(incident)
                    sources_list.append(cap)
                except Exception as exc:
                    logger.warning(
                        "Failed to query capability from %s adapter: %s",
                        source_type.value,
                        exc,
                    )
                    sources_list.append(
                        get_default_capability(source_type, available=False)
                    )
                continue

            # 3. No adapter or capability registered -> mark unavailable
            sources_list.append(
                get_default_capability(source_type, available=False)
            )

        return SourceCapabilityCatalog(
            schema_version="1.0",
            incident_id=incident.incident_id,
            generated_at=self.clock.now(),
            sources=sources_list,
        )
