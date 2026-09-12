"""Reusable repository behavior suite for every storage adapter.

A future adapter opts in by mixing ``RepositoryContract`` into a unittest
class and implementing ``create_repositories``. The assertions intentionally
use only domain ports and contracts.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceFilter, EvidenceRecord
from contracts.timeline import Timeline
from evidence.repositories.ports import (
    EvidenceOrder,
    EvidencePageRequest,
    RepositoryConflictError,
    RepositoryValidationError,
)


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def evidence(
    evidence_id: str = "ev_201",
    *,
    incident_id: str = "inc_001",
    event_offset_seconds: int | None = 0,
    source_type: str = "logs",
) -> EvidenceRecord:
    value = _fixture("evidence_record.json")
    value["evidence_id"] = evidence_id
    value["incident_id"] = incident_id
    value["source_type"] = source_type
    value["provenance"]["source_record_id"] = evidence_id
    if event_offset_seconds is None:
        value.pop("event_time", None)
    else:
        base = datetime(2026, 9, 12, 10, 27, 15, tzinfo=timezone.utc)
        value["event_time"] = (base + timedelta(seconds=event_offset_seconds)).isoformat()
    return EvidenceRecord.from_dict(value)


def snapshot(
    snapshot_id: str = "ctx_501", *, incident_id: str = "inc_001", revision: int = 3
) -> IncidentContextSnapshot:
    value = _fixture("incident_context_snapshot.json")
    value["snapshot_id"] = snapshot_id
    value["incident_id"] = incident_id
    value["revision"] = revision
    for event in value["timeline"]:
        event["incident_id"] = incident_id
    for relationship in value["relationships"]:
        relationship["incident_id"] = incident_id
    return IncidentContextSnapshot.from_dict(value)


class RepositoryContract:
    """Mixin containing the adapter-independent repository contract tests."""

    def create_repositories(self) -> tuple[Any, Any, Any]:
        raise NotImplementedError

    def setUp(self) -> None:
        self.evidence_repository, self.timeline_repository, self.context_repository = (
            self.create_repositories()
        )

    def test_evidence_save_get_and_not_found(self) -> None:
        record = evidence()
        self.assertEqual(self.evidence_repository.save_all((record,)), (record.evidence_id,))
        self.assertEqual(self.evidence_repository.get(record.evidence_id), record)
        self.assertIsNone(self.evidence_repository.get("ev_missing"))

    def test_evidence_identical_save_is_idempotent(self) -> None:
        record = evidence()
        self.evidence_repository.save_all((record,))
        self.assertEqual(self.evidence_repository.save_all((record,)), (record.evidence_id,))
        self.assertEqual(
            self.evidence_repository.query(
                EvidenceFilter(schema_version="1.0", incident_id="inc_001")
            ),
            (record,),
        )

    def test_evidence_conflict_rejects_entire_batch_atomically(self) -> None:
        original = evidence()
        self.evidence_repository.save_all((original,))
        incompatible = replace(original, summary="Different immutable content")
        new_record = evidence("ev_new")
        with self.assertRaises(RepositoryConflictError):
            self.evidence_repository.save_all((new_record, incompatible))
        self.assertIsNone(self.evidence_repository.get("ev_new"))
        self.assertEqual(self.evidence_repository.get(original.evidence_id), original)

    def test_evidence_query_is_incident_isolated_and_filtered(self) -> None:
        selected = evidence("ev_selected", source_type="logs")
        wrong_source = evidence("ev_metric", source_type="metrics")
        other_incident = evidence("ev_other", incident_id="inc_other")
        self.evidence_repository.save_all((other_incident, wrong_source, selected))
        criteria = EvidenceFilter(
            schema_version="1.0",
            incident_id="inc_001",
            source_types=("logs",),
        )
        self.assertEqual(self.evidence_repository.query(criteria), (selected,))

    def test_evidence_stable_order_limit_and_unknown_time(self) -> None:
        records = (
            evidence("ev_z", event_offset_seconds=0),
            evidence("ev_unknown", event_offset_seconds=None),
            evidence("ev_later", event_offset_seconds=60),
            evidence("ev_a", event_offset_seconds=0),
        )
        self.evidence_repository.save_all(records)
        criteria = EvidenceFilter(
            schema_version="1.0",
            incident_id="inc_001",
            include_unknown_event_time=True,
            limit=3,
        )
        self.assertEqual(
            tuple(item.evidence_id for item in self.evidence_repository.query(criteria)),
            ("ev_a", "ev_z", "ev_later"),
        )

    def test_evidence_cursor_pagination_and_descending_order(self) -> None:
        self.evidence_repository.save_all(
            tuple(evidence(f"ev_{index}", event_offset_seconds=index) for index in range(3))
        )
        criteria = EvidenceFilter(schema_version="1.0", incident_id="inc_001", limit=3)
        request = EvidencePageRequest(limit=2, order=EvidenceOrder.EVENT_TIME_DESC)
        first = self.evidence_repository.query_page(criteria, request)
        second = self.evidence_repository.query_page(
            criteria,
            EvidencePageRequest(
                limit=2, cursor=first.next_cursor, order=EvidenceOrder.EVENT_TIME_DESC
            ),
        )
        self.assertEqual(tuple(item.evidence_id for item in first.items), ("ev_2", "ev_1"))
        self.assertEqual(tuple(item.evidence_id for item in second.items), ("ev_0",))
        self.assertIsNone(second.next_cursor)

    def test_invalid_or_wrong_order_cursor_is_translated(self) -> None:
        criteria = EvidenceFilter(schema_version="1.0", incident_id="inc_001")
        with self.assertRaises(RepositoryValidationError):
            self.evidence_repository.query_page(
                criteria, EvidencePageRequest(cursor="not-a-cursor")
            )

    def test_timeline_replace_revision_and_latest_semantics(self) -> None:
        base = snapshot(revision=1).timeline
        replacement = Timeline(
            incident_id="inc_001", events=tuple(reversed(base.events)), relationships=base.relationships
        )
        later = Timeline(incident_id="inc_001", events=(), relationships=())
        self.timeline_repository.replace_revision(
            "inc_001", 1, base.events, base.relationships
        )
        self.timeline_repository.replace_revision(
            "inc_001", 1, replacement.events, replacement.relationships
        )
        self.timeline_repository.replace_revision(
            "inc_001", 2, later.events, later.relationships
        )
        self.assertEqual(self.timeline_repository.get_revision("inc_001", 1), base)
        self.assertEqual(self.timeline_repository.get_latest("inc_001"), later)
        self.assertIsNone(self.timeline_repository.get_latest("inc_missing"))

    def test_timeline_rejects_incident_mismatch(self) -> None:
        with self.assertRaises(RepositoryValidationError):
            self.timeline_repository.replace_revision(
                "inc_other", 1, snapshot().timeline.events, ()
            )

    def test_timeline_results_have_stable_event_order(self) -> None:
        original = snapshot().timeline.events[0]
        same_time_later_id = replace(original, timeline_event_id="tle_z")
        same_time_earlier_id = replace(original, timeline_event_id="tle_a")
        unknown_time = replace(
            original, timeline_event_id="tle_unknown", event_time=None
        )
        self.timeline_repository.replace_revision(
            "inc_001",
            1,
            (unknown_time, same_time_later_id, same_time_earlier_id),
            (),
        )
        stored = self.timeline_repository.get_latest("inc_001")
        self.assertIsNotNone(stored)
        self.assertEqual(
            tuple(event.timeline_event_id for event in stored.events),
            ("tle_a", "tle_z", "tle_unknown"),
        )

    def test_context_idempotency_conflict_latest_and_isolation(self) -> None:
        first = snapshot("ctx_first", revision=1)
        later = snapshot("ctx_later", revision=2)
        other = snapshot("ctx_other", incident_id="inc_other", revision=9)
        self.assertEqual(self.context_repository.save(first), "ctx_first")
        self.assertEqual(self.context_repository.save(first), "ctx_first")
        self.context_repository.save(other)
        self.context_repository.save(later)
        self.assertEqual(self.context_repository.get_latest("inc_001"), later)
        self.assertEqual(self.context_repository.get_latest("inc_other"), other)
        self.assertIsNone(self.context_repository.get("ctx_missing"))
        with self.assertRaises(RepositoryConflictError):
            self.context_repository.save(replace(later, snapshot_id="ctx_conflict"))
