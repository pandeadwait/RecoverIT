"""Base protocol for all operational data source adapters.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.1, all adapters are
strictly read-only and implement this protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from contracts.collection.schemas import (
    EvidenceQuery,
    SourceCapability,
    SourceResult,
)
from contracts.enums import SourceType
from contracts.incident.schemas import IncidentSeed


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol for operational data source adapters.

    All implementations must be read-only and must never mutate the
    external system (e.g. no git commits, configuration changes, or workload
    restarts).
    """

    source_type: SourceType
    adapter_name: str

    def get_capability(self, incident: IncidentSeed) -> SourceCapability:
        """Return the capability descriptor for this source given an incident."""
        ...

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Execute a read-only query and return a typed SourceResult."""
        ...
