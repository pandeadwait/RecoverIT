"""Contract and atomicity tests for the in-memory reference adapters."""

from __future__ import annotations

from dataclasses import replace
import unittest

from contracts.evidence import EvidenceFilter
from evidence.context.repositories.memory import InMemoryContextRepository
from evidence.repositories.memory import InMemoryEvidenceRepository
from evidence.repositories.ports import RepositoryConflictError, RepositoryValidationError
from evidence.repositories.state import InMemoryRepositoryState
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator
from tests.repository_contract import RepositoryContract, evidence, snapshot
from timeline.repositories.memory import InMemoryTimelineRepository


class InMemoryRepositoryContractTests(RepositoryContract, unittest.TestCase):
    def create_repositories(self) -> tuple[object, object, object]:
        state = InMemoryRepositoryState()
        return (
            InMemoryEvidenceRepository(state),
            InMemoryTimelineRepository(state),
            InMemoryContextRepository(state),
        )


class InMemoryRepositoryCoordinatorTests(unittest.TestCase):
    def test_revision_write_commits_all_repositories(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        record = evidence()
        context = snapshot(revision=3)
        result = coordinator.save_revision((record,), 3, context.timeline, context)
        self.assertEqual(result, context.snapshot_id)
        self.assertEqual(coordinator.evidence.get(record.evidence_id), record)
        self.assertEqual(coordinator.timelines.get_latest("inc_001"), context.timeline)
        self.assertEqual(coordinator.contexts.get_latest("inc_001"), context)

    def test_revision_write_validates_incident_before_mutation(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        record = evidence(incident_id="inc_other")
        context = snapshot(revision=3)
        with self.assertRaises(RepositoryValidationError):
            coordinator.save_revision((record,), 3, context.timeline, context)
        self.assertIsNone(coordinator.evidence.get(record.evidence_id))

    def test_revision_write_rolls_back_on_late_conflict(self) -> None:
        coordinator = InMemoryRepositoryCoordinator()
        existing = snapshot("ctx_existing", revision=3)
        coordinator.contexts.save(existing)
        new_record = evidence("ev_new")
        conflicting = replace(existing, snapshot_id="ctx_conflicting")
        with self.assertRaises(RepositoryConflictError):
            coordinator.save_revision((new_record,), 3, existing.timeline, conflicting)
        self.assertIsNone(coordinator.evidence.get("ev_new"))
        self.assertIsNone(coordinator.timelines.get_latest("inc_001"))
        self.assertEqual(coordinator.contexts.get_latest("inc_001"), existing)

    def test_processing_error_translation_hides_adapter_types(self) -> None:
        error = RepositoryConflictError("immutable record conflict")
        translated = error.to_processing_error()
        self.assertEqual(translated.code, "x-storage-conflict")
        self.assertFalse(translated.retryable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
