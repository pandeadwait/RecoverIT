"""Domain-owned timeline persistence contract."""

from __future__ import annotations

from typing import Protocol

from contracts.timeline import TemporalRelationship, Timeline, TimelineEvent


class TimelineRepository(Protocol):
    """Stores replaceable timeline revisions and returns the greatest revision."""

    def replace_revision(
        self,
        incident_id: str,
        revision: int,
        events: tuple[TimelineEvent, ...],
        relationships: tuple[TemporalRelationship, ...],
    ) -> None: ...

    def get_latest(self, incident_id: str) -> Timeline | None: ...

    def get_revision(self, incident_id: str, revision: int) -> Timeline | None: ...
