"""Tests for DefaultContextBuilder and canonical context construction."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceResult,
)
from contracts.errors.schemas import StructuredError
from contracts.common import (
    Reliability,
    RelationshipType,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
)
from contracts.evidence.schemas import (
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.incident.schemas import IncidentSeed
from evidence.context.builder import ContextBuilder, DefaultContextBuilder
from investigation.graph.ports import ContextBuilder as GraphContextBuilderPort


def _make_incident_seed() -> IncidentSeed:
    now = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    return IncidentSeed(
        incident_id="inc-test-456",
        external_alert_id="alert-456",
        service="payment-gateway",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=now,
        received_at=now,
        summary="Elevated error rates on checkout payment processing",
    )


def _make_initial_context(incident: IncidentSeed) -> IncidentContextSnapshot:
    return IncidentContextSnapshot(
        snapshot_id=f"ctx-{incident.incident_id}-0",
        incident_id=incident.incident_id,
        revision=0,
        created_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=incident.service,
            environment=incident.environment,
            severity=incident.severity,
            detected_at=incident.detected_at,
            summary=incident.summary,
        ),
        source_coverage={
            source.value: SourceCoverageStatus.NOT_QUERIED for source in SourceType
        },
    )


class TestDefaultContextBuilder:
    """Validates Person 3's DefaultContextBuilder implementation."""

    def test_implements_frozen_protocol(self) -> None:
        builder = DefaultContextBuilder()
        assert isinstance(builder, ContextBuilder)
        assert isinstance(builder, GraphContextBuilderPort)

    @pytest.mark.asyncio
    async def test_build_initial_revision_with_evidence(self) -> None:
        incident = _make_incident_seed()
        init_ctx = _make_initial_context(incident)
        assert init_ctx.revision == 0
        assert len(init_ctx.evidence) == 0

        t0 = datetime(2026, 3, 15, 9, 50, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 15, 9, 55, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 15, 10, 5, 0, tzinfo=timezone.utc)

        # Construct raw records
        rec1 = RawRecord(
            source_record_id="log-101",
            content_type="application/json",
            payload={"message": "NullPointer in payment provider password=secret1234567890"},
            event_time=t1,
        )
        rec2 = RawRecord(
            source_record_id="dep-202",
            content_type="application/json",
            payload={"title": "Deployed v2.4.1 commit=abcdef auth=bearer eyJhbGciOiJIUzI1NiJ9.test"},
            event_time=t0,
        )

        batch = RawEvidenceBatch(
            batch_id="batch-001",
            incident_id=incident.incident_id,
            plan_id="plan-001",
            collected_at=now,
            results=[
                SourceResult(
                    query_id="q-logs",
                    source_type=SourceType.LOGS,
                    source_adapter="elasticsearch",
                    source_status=SourceStatus.OK,
                    started_at=t0,
                    completed_at=now,
                    records=[rec1],
                ),
                SourceResult(
                    query_id="q-dep",
                    source_type=SourceType.DEPLOYMENTS,
                    source_adapter="argocd",
                    source_status=SourceStatus.OK,
                    started_at=t0,
                    completed_at=now,
                    records=[rec2],
                ),
                SourceResult(
                    query_id="q-met",
                    source_type=SourceType.METRICS,
                    source_adapter="prometheus",
                    source_status=SourceStatus.EMPTY,
                    started_at=t0,
                    completed_at=now,
                    records=[],
                ),
            ],
            errors=[],
        )

        builder = DefaultContextBuilder()
        next_ctx = await builder.build(incident, batch, init_ctx)

        # 1. Revision increments
        assert next_ctx.revision == 1
        assert next_ctx.incident_id == incident.incident_id

        # 2. Evidence projections
        assert len(next_ctx.evidence) == 2
        ev_logs = next(e for e in next_ctx.evidence if "log" in e.evidence_id)
        assert ev_logs.quality.reliability == Reliability.HIGH
        assert ev_logs.quality.redactions_applied is True
        assert "secret1234567890" not in ev_logs.summary
        assert "[REDACTED]" in ev_logs.summary

        # 3. Timeline deterministic ordering (dep-202 at 09:50 precedes log-101 at 09:55)
        assert len(next_ctx.timeline) == 2
        assert next_ctx.timeline[0].event_time <= next_ctx.timeline[1].event_time
        assert "dep-202" in next_ctx.timeline[0].timeline_event_id

        # 4. Temporal relationships
        assert len(next_ctx.relationships) == 1
        rel = next_ctx.relationships[0]
        assert rel.relationship_type == RelationshipType.PRECEDES
        assert rel.delta_ms == 300_000  # 5 minutes difference

        # 5. Coverage across canonical 7 sources
        assert len(next_ctx.source_coverage) == 7
        assert next_ctx.source_coverage["logs"] == SourceCoverageStatus.AVAILABLE
        assert next_ctx.source_coverage["deployments"] == SourceCoverageStatus.AVAILABLE
        assert next_ctx.source_coverage["metrics"] == SourceCoverageStatus.EMPTY
        assert next_ctx.source_coverage["health"] == SourceCoverageStatus.NOT_QUERIED
        assert next_ctx.source_coverage["pipelines"] == SourceCoverageStatus.NOT_QUERIED

    @pytest.mark.asyncio
    async def test_incremental_deduplication_and_warnings(self) -> None:
        incident = _make_incident_seed()
        init_ctx = _make_initial_context(incident)
        builder = DefaultContextBuilder()

        t0 = datetime(2026, 3, 15, 9, 50, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 15, 9, 52, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 15, 10, 5, 0, tzinfo=timezone.utc)

        rec1 = RawRecord(
            source_record_id="log-101",
            content_type="application/json",
            payload={"message": "Service starting"},
            event_time=t0,
        )
        batch1 = RawEvidenceBatch(
            batch_id="batch-001",
            incident_id=incident.incident_id,
            plan_id="plan-001",
            collected_at=now,
            results=[
                SourceResult(
                    query_id="q-logs-1",
                    source_type=SourceType.LOGS,
                    source_adapter="elasticsearch",
                    source_status=SourceStatus.OK,
                    started_at=t0,
                    completed_at=now,
                    records=[rec1],
                    warnings=["Slow log query latency"],
                )
            ],
            errors=[],
        )

        ctx_rev1 = await builder.build(incident, batch1, init_ctx)
        assert ctx_rev1.revision == 1
        assert len(ctx_rev1.evidence) == 1
        assert any("Slow log query latency" in str(w) for w in ctx_rev1.warnings)

        # Batch 2 contains same record + new record + collection error
        rec2 = RawRecord(
            source_record_id="log-102",
            content_type="application/json",
            payload={"message": "Database pool exhausted"},
            event_time=t1,
        )
        batch2 = RawEvidenceBatch(
            batch_id="batch-002",
            incident_id=incident.incident_id,
            plan_id="plan-002",
            collected_at=now,
            results=[
                SourceResult(
                    query_id="q-logs-2",
                    source_type=SourceType.LOGS,
                    source_adapter="elasticsearch",
                    source_status=SourceStatus.OK,
                    started_at=t0,
                    completed_at=now,
                    records=[rec1, rec2],
                )
            ],
            errors=[
                StructuredError(
                    code="TIMEOUT",
                    message="Prometheus query timed out",
                    stage="collection",
                    source_type=SourceType.METRICS,
                )
            ],
        )

        ctx_rev2 = await builder.build(incident, batch2, ctx_rev1)
        assert ctx_rev2.revision == 2
        # Should deduplicate rec1
        assert len(ctx_rev2.evidence) == 2
        assert len(ctx_rev2.timeline) == 2
        # Should contain error warning and preserve previous warnings without duplicates
        assert any("TIMEOUT" in str(w) for w in ctx_rev2.warnings)
        assert any("Slow log query latency" in str(w) for w in ctx_rev2.warnings)

    @pytest.mark.asyncio
    async def test_quarantined_secrets_handling(self) -> None:
        incident = _make_incident_seed()
        init_ctx = _make_initial_context(incident)
        builder = DefaultContextBuilder()

        t0 = datetime(2026, 3, 15, 9, 50, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 15, 10, 5, 0, tzinfo=timezone.utc)

        # RSA private key material should trigger quarantine
        private_key_pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----"
        rec_quarantine = RawRecord(
            source_record_id="cfg-999",
            content_type="application/json",
            payload={"ssl_key": private_key_pem},
            event_time=t0,
        )

        batch = RawEvidenceBatch(
            batch_id="batch-quarantine",
            incident_id=incident.incident_id,
            plan_id="plan-quarantine",
            collected_at=now,
            results=[
                SourceResult(
                    query_id="q-cfg",
                    source_type=SourceType.CONFIGURATION,
                    source_adapter="k8s",
                    source_status=SourceStatus.OK,
                    started_at=t0,
                    completed_at=now,
                    records=[rec_quarantine],
                )
            ],
            errors=[],
        )

        ctx = await builder.build(incident, batch, init_ctx)
        # Quarantined record should NOT be in evidence projections
        assert len(ctx.evidence) == 0
        # Quarantine warning should be recorded
        assert any("quarantined" in str(w).lower() for w in ctx.warnings)
