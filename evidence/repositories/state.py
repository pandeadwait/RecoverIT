"""Shared state used by the in-memory adapters and atomic coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from contracts.context import IncidentContextSnapshot
from contracts.evidence import EvidenceRecord
from contracts.timeline import Timeline
from evidence.deduplication.models import ProvenanceAttachment, RepeatedEventAggregate


@dataclass(slots=True)
class InMemoryRepositoryState:
    lock: RLock = field(default_factory=RLock)
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    aggregates: dict[str, RepeatedEventAggregate] = field(default_factory=dict)
    provenance_attachments: dict[tuple[str, bytes], ProvenanceAttachment] = field(
        default_factory=dict
    )
    timelines: dict[tuple[str, int], Timeline] = field(default_factory=dict)
    contexts: dict[str, IncidentContextSnapshot] = field(default_factory=dict)
    context_revisions: dict[tuple[str, int], str] = field(default_factory=dict)
