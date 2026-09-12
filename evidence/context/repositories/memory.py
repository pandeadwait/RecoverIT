"""Thread-safe in-memory reference context repository."""

from __future__ import annotations

from contracts.common import canonical_bytes, require_identifier
from contracts.context import IncidentContextSnapshot
from evidence.context.repositories.ports import ContextRepository
from evidence.repositories.ports import RepositoryConflictError
from evidence.repositories.state import InMemoryRepositoryState


class InMemoryContextRepository(ContextRepository):
    def __init__(self, state: InMemoryRepositoryState | None = None) -> None:
        self._state = state or InMemoryRepositoryState()

    def save(self, snapshot: IncidentContextSnapshot) -> str:
        revision_key = (snapshot.incident_id, snapshot.revision)
        with self._state.lock:
            existing = self._state.contexts.get(snapshot.snapshot_id)
            existing_revision_id = self._state.context_revisions.get(revision_key)
            if existing is not None and canonical_bytes(existing) != canonical_bytes(snapshot):
                raise RepositoryConflictError(
                    f"snapshot ID {snapshot.snapshot_id!r} already stores different immutable content"
                )
            if existing_revision_id is not None and existing_revision_id != snapshot.snapshot_id:
                raise RepositoryConflictError(
                    f"incident revision {snapshot.revision} already belongs to another snapshot"
                )
            self._state.contexts[snapshot.snapshot_id] = snapshot
            self._state.context_revisions[revision_key] = snapshot.snapshot_id
            return snapshot.snapshot_id

    def get_latest(self, incident_id: str) -> IncidentContextSnapshot | None:
        require_identifier(incident_id, "incident_id")
        with self._state.lock:
            revisions = [
                revision
                for stored_incident, revision in self._state.context_revisions
                if stored_incident == incident_id
            ]
            if not revisions:
                return None
            snapshot_id = self._state.context_revisions[(incident_id, max(revisions))]
            return self._state.contexts[snapshot_id]

    def get(self, snapshot_id: str) -> IncidentContextSnapshot | None:
        require_identifier(snapshot_id, "snapshot_id")
        with self._state.lock:
            return self._state.contexts.get(snapshot_id)
