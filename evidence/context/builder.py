"""Deterministic construction of Person 2's context snapshot contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from contracts.collection.schemas import RawEvidenceBatch
from contracts.common import SCHEMA_VERSION, canonical_bytes
from contracts.context import (
    EvidenceProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceRecord
from contracts.incident import IncidentSeed
from contracts.primitives import (
    Clock,
    DeterministicIdGenerator,
    IdGenerator,
    SystemClock,
)
from contracts.timeline import Timeline, TimelineEvent
from evidence.context.coverage import SourceCoverageCalculator
from evidence.security.redaction import Redactor
from investigation.graph.ports import ContextBuilder


class ContextBuildError(ValueError):
    """Inputs cannot form a complete incident context snapshot."""


class ContextSnapshotBuilder:
    """Builds a compact, reference-valid, immutable context revision."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._ids = id_generator or DeterministicIdGenerator()

    def build(
        self,
        incident: IncidentSeed,
        records: tuple[EvidenceRecord, ...],
        timeline: Timeline,
        source_coverage: Mapping[str, str],
        warnings: tuple[ProcessingWarning, ...] = (),
        previous_snapshot: IncidentContextSnapshot | None = None,
    ) -> IncidentContextSnapshot:
        self._validate_inputs(incident, records, timeline, previous_snapshot)
        revision = 1 if previous_snapshot is None else previous_snapshot.revision + 1
        created_at = self._clock.now()
        if previous_snapshot is not None and created_at < previous_snapshot.created_at:
            raise ContextBuildError("snapshot clock cannot move behind previous revision")
        ordered_records = tuple(sorted(records, key=self._record_sort_key))
        ordered_timeline = self._ordered_timeline(timeline)
        merged_warnings = self._merge_warnings(
            previous_snapshot.warnings if previous_snapshot is not None else (),
            warnings,
        )
        projections = tuple(self._projection(record) for record in ordered_records)
        incident_summary = IncidentSummary(
            service=incident.service,
            environment=incident.environment,
            severity=incident.severity,
            detected_at=incident.detected_at,
            summary=incident.summary,
        )
        identity_material = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": incident.incident_id,
            "revision": revision,
            "created_at": created_at,
            "incident": incident_summary,
            "evidence": projections,
            "timeline": ordered_timeline,
            "source_coverage": dict(source_coverage),
            "warnings": merged_warnings,
        }
        return IncidentContextSnapshot(
            schema_version=SCHEMA_VERSION,
            snapshot_id=self._ids.create("ctx", identity_material),
            incident_id=incident.incident_id,
            revision=revision,
            created_at=created_at,
            incident=incident_summary,
            evidence=projections,
            timeline=ordered_timeline,
            source_coverage=source_coverage,
            warnings=merged_warnings,
        )

    @staticmethod
    def _validate_inputs(
        incident: IncidentSeed,
        records: tuple[EvidenceRecord, ...],
        timeline: Timeline,
        previous_snapshot: IncidentContextSnapshot | None,
    ) -> None:
        if any(record.incident_id != incident.incident_id for record in records):
            raise ContextBuildError("all evidence must belong to the snapshot incident")
        evidence_ids = [record.evidence_id for record in records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ContextBuildError("snapshot evidence IDs must be unique")
        if timeline.incident_id != incident.incident_id:
            raise ContextBuildError("timeline must belong to the snapshot incident")
        referenced = [
            evidence_id for event in timeline.events for evidence_id in event.evidence_ids
        ]
        if len(referenced) != len(set(referenced)):
            raise ContextBuildError(
                "timeline must not reference an evidence record more than once"
            )
        if set(referenced) != set(evidence_ids):
            raise ContextBuildError(
                "timeline must reference every persisted evidence record exactly once"
            )
        if previous_snapshot is not None:
            if previous_snapshot.incident_id != incident.incident_id:
                raise ContextBuildError("previous snapshot belongs to another incident")
            if previous_snapshot.incident.to_dict() != IncidentSummary(
                service=incident.service,
                environment=incident.environment,
                severity=incident.severity,
                detected_at=incident.detected_at,
                summary=incident.summary,
            ).to_dict():
                raise ContextBuildError("incident identity changed across snapshot revisions")

    @staticmethod
    def _record_sort_key(record: EvidenceRecord) -> tuple[int, datetime | None, str]:
        return (
            1 if record.event_time is None else 0,
            record.event_time,
            record.evidence_id,
        )

    @staticmethod
    def _event_sort_key(
        event: TimelineEvent,
    ) -> tuple[int, datetime | None, tuple[str, ...], str]:
        return (
            1 if event.event_time is None else 0,
            event.event_time,
            event.evidence_ids,
            event.timeline_event_id,
        )

    @classmethod
    def _ordered_timeline(cls, timeline: Timeline) -> Timeline:
        return Timeline(
            incident_id=timeline.incident_id,
            events=tuple(sorted(timeline.events, key=cls._event_sort_key)),
            relationships=tuple(
                sorted(timeline.relationships, key=lambda item: item.relationship_id)
            ),
        )

    @staticmethod
    def _projection(record: EvidenceRecord) -> EvidenceProjection:
        return EvidenceProjection(
            evidence_id=record.evidence_id,
            source_type=record.source_type,
            evidence_type=record.evidence_type,
            event_time=record.event_time,
            summary=record.summary,
            quality=record.quality,
        )

    @staticmethod
    def _merge_warnings(
        previous: tuple[ProcessingWarning, ...],
        current: tuple[ProcessingWarning, ...],
    ) -> tuple[ProcessingWarning, ...]:
        unique = {canonical_bytes(item): item for item in previous + current}
        return tuple(unique[key] for key in sorted(unique))


# ---------------------------------------------------------------------------
# Canonical ContextBuilder Protocol Implementation (Person 3)
# ---------------------------------------------------------------------------


class DefaultContextBuilder:
    """Canonical ContextBuilder satisfying the frozen graph protocol.

    Transforms raw evidence batches into normalized, redacted, deduplicated evidence,
    constructs a deterministic timeline with temporal relationships, updates
    source coverage across all seven source types, and emits canonical
    IncidentContextSnapshot revisions.
    """

    def __init__(
        self,
        redactor: Redactor | None = None,
        coverage_calculator: SourceCoverageCalculator | None = None,
    ) -> None:
        self._redactor = redactor or Redactor()
        self._coverage = coverage_calculator or SourceCoverageCalculator()

    async def build(
        self,
        incident: IncidentSeed,
        batch: RawEvidenceBatch,
        previous_context: IncidentContextSnapshot,
    ) -> IncidentContextSnapshot:
        from datetime import timezone
        from contracts.common import (
            EvidenceType,
            Reliability,
            RelationshipCreator,
            RelationshipType,
            Severity,
            SourceCoverageStatus,
            SourceStatus,
            SourceType,
            TimelineCategory,
        )
        from contracts.evidence.schemas import (
            EvidenceQuality,
            EvidenceSummaryProjection,
            IncidentContextSnapshot,
            IncidentSummary,
            TemporalRelationship,
            TimelineEventProjection,
        )
        from evidence.security.models import RedactionOutcome

        # 1. Process new raw records from batch
        new_evidence: list[EvidenceSummaryProjection] = []
        captured_warnings: list[str | dict[str, Any]] = []

        for res in batch.results:
            st = res.source_type
            st_val = st.value if hasattr(st, "value") else str(st)
            for rec in res.records:
                # Redact payload
                redaction_result = self._redactor.redact(rec.payload)
                if redaction_result.outcome is RedactionOutcome.QUARANTINE:
                    captured_warnings.append(
                        f"Record {rec.source_record_id} from {st_val} quarantined: sensitive key material"
                    )
                    continue

                # Extract and redact summary
                payload_dict = (
                    redaction_result.value
                    if isinstance(redaction_result.value, Mapping)
                    else {}
                )
                summary_raw = (
                    payload_dict.get("message")
                    or payload_dict.get("summary")
                    or payload_dict.get("commit_message")
                    or payload_dict.get("title")
                    or f"{st_val} record {rec.source_record_id}"
                )
                redacted_summary = str(self._redactor.redact(str(summary_raw)).value)

                # Classify evidence type
                ev_type = self._classify_evidence_type(st, payload_dict)

                # Reliability
                reliability = (
                    Reliability.HIGH
                    if res.source_status == SourceStatus.OK
                    else Reliability.MEDIUM
                )
                redactions_applied = bool(
                    getattr(redaction_result, "redactions_applied", False)
                    or getattr(redaction_result, "redacted_paths", ())
                )
                quality = EvidenceQuality(
                    reliability=reliability,
                    freshness_seconds=0,
                    redactions_applied=redactions_applied,
                )

                ev_id = f"ev_{st_val}_{rec.source_record_id}"
                event_time = rec.event_time or rec.observed_at or previous_context.created_at

                new_evidence.append(
                    EvidenceSummaryProjection(
                        evidence_id=ev_id,
                        source_type=st,
                        evidence_type=ev_type,
                        event_time=event_time,
                        summary=redacted_summary,
                        quality=quality,
                    )
                )

        # 2. Merge and deduplicate with existing evidence
        all_evidence: list[EvidenceSummaryProjection] = list(previous_context.evidence)
        existing_ids = {e.evidence_id for e in all_evidence}
        for item in new_evidence:
            if item.evidence_id not in existing_ids:
                all_evidence.append(item)
                existing_ids.add(item.evidence_id)

        all_evidence.sort(
            key=lambda e: (
                1 if e.event_time is None else 0,
                e.event_time,
                e.evidence_id,
            )
        )

        # 3. Build chronological timeline
        timeline_events: list[TimelineEventProjection] = []
        for ev in all_evidence:
            category = self._category_for_source_and_type(ev.source_type, ev.evidence_type)
            tle_id = f"tle_{ev.evidence_id}"
            timeline_events.append(
                TimelineEventProjection(
                    timeline_event_id=tle_id,
                    incident_id=incident.incident_id,
                    event_time=ev.event_time,
                    category=category,
                    title=ev.summary,
                    service=incident.service,
                    evidence_ids=[ev.evidence_id],
                )
            )

        timeline_events.sort(
            key=lambda t: (
                1 if t.event_time is None else 0,
                t.event_time,
                t.timeline_event_id,
            )
        )

        # 4. Calculate temporal relationships
        relationships: list[TemporalRelationship] = []
        events_with_time = [e for e in timeline_events if e.event_time is not None]
        for e1, e2 in zip(events_with_time, events_with_time[1:]):
            delta = int(abs((e2.event_time - e1.event_time).total_seconds() * 1000))
            rel_type = (
                RelationshipType.COINCIDES_WITH
                if delta == 0
                else RelationshipType.PRECEDES
            )
            rel_id = f"rel_{e1.timeline_event_id}_{e2.timeline_event_id}"
            relationships.append(
                TemporalRelationship(
                    relationship_id=rel_id,
                    incident_id=incident.incident_id,
                    from_event_id=e1.timeline_event_id,
                    to_event_id=e2.timeline_event_id,
                    relationship_type=rel_type,
                    delta_ms=delta,
                    created_by=RelationshipCreator.DETERMINISTIC,
                )
            )

        # 5. Calculate source coverage for all 7 sources
        canonical_source_types = tuple(st.value for st in SourceType)
        raw_cov = self._coverage.calculate(
            (batch,),
            previous_context.source_coverage,
            source_types=canonical_source_types,
        )
        source_coverage: dict[str, SourceCoverageStatus] = {}
        for source_enum in SourceType:
            source_key = source_enum.value
            status_str = raw_cov.get(source_key, "not_queried")
            try:
                source_coverage[source_key] = SourceCoverageStatus(status_str)
            except ValueError:
                source_coverage[source_key] = SourceCoverageStatus.NOT_QUERIED

        # 6. Merge warnings and errors
        merged_warnings: list[str | dict[str, Any]] = list(previous_context.warnings)
        merged_warnings.extend(captured_warnings)
        for err in batch.errors:
            err_src = getattr(err, "source", getattr(err, "source_type", "unknown"))
            merged_warnings.append(
                f"Collection error [{err.code}] on {err_src}: {err.message}"
            )
        for res in batch.results:
            for w in res.warnings:
                merged_warnings.append(f"Collection warning on {res.source_type}: {w}")

        # Deduplicate warnings while preserving order
        deduped_warnings: list[str | dict[str, Any]] = []
        seen_warns: set[str] = set()
        for w in merged_warnings:
            w_str = str(w)
            if w_str not in seen_warns:
                seen_warns.add(w_str)
                deduped_warnings.append(w)

        # 7. Construct next context revision
        revision = previous_context.revision + 1
        snapshot_id = f"ctx_{incident.incident_id}_{revision}"
        created_at = datetime.now(timezone.utc)

        return IncidentContextSnapshot(
            snapshot_id=snapshot_id,
            incident_id=incident.incident_id,
            revision=revision,
            created_at=created_at,
            incident=previous_context.incident,
            evidence=all_evidence,
            timeline=timeline_events,
            relationships=relationships,
            source_coverage=source_coverage,
            warnings=deduped_warnings,
        )

    @staticmethod
    def _classify_evidence_type(source_type: Any, payload: dict[str, Any]) -> Any:
        from contracts.common import EvidenceType, SourceType

        if source_type == SourceType.LOGS:
            payload_str = str(payload).lower()
            if any(term in payload_str for term in ("error", "fail", "500", "exception", "timeout")):
                return EvidenceType.ERROR_EVENT
            return EvidenceType.LOG_EVENT
        elif source_type == SourceType.METRICS:
            return EvidenceType.METRIC_ANOMALY
        elif source_type == SourceType.CHANGES:
            return EvidenceType.CODE_CHANGE
        elif source_type == SourceType.DEPLOYMENTS:
            return EvidenceType.DEPLOYMENT_EVENT
        elif source_type == SourceType.CONFIGURATION:
            return EvidenceType.CONFIGURATION_CHANGE
        elif source_type == SourceType.PIPELINES:
            return EvidenceType.PIPELINE_EVENT
        elif source_type == SourceType.HEALTH:
            return EvidenceType.HEALTH_CHECK
        return EvidenceType.ERROR_EVENT

    @staticmethod
    def _category_for_source_and_type(source_type: Any, evidence_type: Any) -> Any:
        from contracts.common import SourceType, TimelineCategory

        if source_type in (SourceType.CHANGES, SourceType.CONFIGURATION, SourceType.PIPELINES):
            return TimelineCategory.CHANGE
        elif source_type == SourceType.DEPLOYMENTS:
            return TimelineCategory.DEPLOYMENT
        elif source_type in (SourceType.LOGS, SourceType.METRICS):
            return TimelineCategory.SYMPTOM
        elif source_type == SourceType.HEALTH:
            return TimelineCategory.VERIFICATION
        return TimelineCategory.SYMPTOM
