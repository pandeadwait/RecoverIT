"""Domain-owned context snapshot persistence contract."""

from __future__ import annotations

from typing import Protocol

from contracts.context import IncidentContextSnapshot


class ContextRepository(Protocol):
    """Stores immutable snapshots and resolves latest by greatest revision."""

    def save(self, snapshot: IncidentContextSnapshot) -> str: ...

    def get_latest(self, incident_id: str) -> IncidentContextSnapshot | None: ...

    def get(self, snapshot_id: str) -> IncidentContextSnapshot | None: ...
