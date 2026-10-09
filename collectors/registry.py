"""Source registry managing source adapter registration and capability catalogs.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.2, this registry owns
registration and discovery of operational source capabilities.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Iterable

from collectors.base import SourceAdapter
from contracts.collection.schemas import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.enums import SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)


class DuplicateSourceAdapterError(ValueError):
    """Raised when an adapter is registered for an already registered SourceType."""

    def __init__(self, source_type: SourceType, existing_adapter: str, new_adapter: str) -> None:
        super().__init__(
            f"Duplicate adapter for source type '{source_type.value}': "
            f"cannot register '{new_adapter}', '{existing_adapter}' is already registered."
        )
        self.source_type = source_type
        self.existing_adapter = existing_adapter
        self.new_adapter = new_adapter


class SourceRegistry:
    """Registry managing source adapters and building capability catalogs.

    Invariants:
    - Registering two adapters for the same source type raises DuplicateSourceAdapterError.
    - capabilities() returns exactly one capability per SourceType across all 7 canonical source types.
    - Unregistered source types are reported as available=False.
    - Adapter discovery errors are sanitized and reported as available=False without raising.
    - No branching on incident service name or scenario identifier.
    """

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self._adapters: dict[SourceType, SourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        """Register a source adapter.

        Raises DuplicateSourceAdapterError if an adapter for adapter.source_type is already registered.
        """
        st = adapter.source_type
        if st in self._adapters:
            existing = self._adapters[st].adapter_name
            raise DuplicateSourceAdapterError(st, existing, adapter.adapter_name)
        self._adapters[st] = adapter

    def get(self, source_type: SourceType) -> SourceAdapter | None:
        """Retrieve the registered adapter for a source type, or None if unregistered."""
        return self._adapters.get(source_type)

    def capabilities(self, incident: IncidentSeed) -> SourceCapabilityCatalog:
        """Build a SourceCapabilityCatalog for the incident.

        All seven canonical source types are represented. Unregistered or failing
        sources are returned with available=False.
        """
        capabilities_list: list[SourceCapability] = []

        for st in SourceType:
            adapter = self._adapters.get(st)
            if adapter is None:
                capabilities_list.append(
                    SourceCapability(
                        source_type=st,
                        available=False,
                        supported_query_fields=[],
                        maximum_window_seconds=0,
                        maximum_items=0,
                        adapter_name="none",
                        unavailable_reason=f"Source type '{st.value}' is not configured.",
                    )
                )
                continue

            try:
                cap = adapter.get_capability(incident)
                capabilities_list.append(cap)
            except Exception as exc:
                logger.warning(
                    "Capability query failed for adapter '%s' (%s): %s",
                    adapter.adapter_name,
                    st.value,
                    exc,
                )
                capabilities_list.append(
                    SourceCapability(
                        source_type=st,
                        available=False,
                        supported_query_fields=[],
                        maximum_window_seconds=0,
                        maximum_items=0,
                        adapter_name=adapter.adapter_name,
                        unavailable_reason="Source capability discovery failed.",
                    )
                )

        return SourceCapabilityCatalog(
            incident_id=incident.incident_id,
            generated_at=datetime.now(timezone.utc),
            sources=capabilities_list,
        )
