"""Unit tests for DefaultCollectionService.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.3:
- Validate every query against the corresponding source capability.
- Reject unknown parameters.
- Enforce maximum time window and result limit.
- Run independent queries concurrently with bounded concurrency.
- Apply an individual timeout to each query.
- Convert source exceptions into SourceResult plus StructuredError.
- Preserve one result per query, including failed queries.
- Never synthesize successful evidence when a source fails.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from collectors.registry import SourceRegistry
from collectors.service import DefaultCollectionService
from contracts.collection.schemas import (
    EvidenceQuery,
    EvidenceQueryPlan,
    RawEvidenceBatch,
    RawRecord,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import Severity
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed
from tests.support.scripted_source import ScriptedSourceAdapter


@pytest.fixture
def sample_incident() -> IncidentSeed:
    now = datetime.now(timezone.utc)
    return IncidentSeed(
        incident_id="inc-svc-001",
        external_alert_id="alt-svc-001",
        service="checkout-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=now,
        received_at=now,
        summary="Collection Service Test Incident",
    )


@pytest.mark.asyncio
async def test_successful_collection_with_preserved_results(sample_incident: IncidentSeed):
    """Collection service collects evidence and aggregates into RawEvidenceBatch."""
    now = datetime.now(timezone.utc)
    record1 = RawRecord(
        source_record_id="rec-1",
        content_type="application/json",
        event_time=now,
        observed_at=now,
        payload={"message": "db error"},
    )
    log_adapter = ScriptedSourceAdapter(
        source_type=SourceType.LOGS,
        adapter_name="test_logs",
        canned_records=[record1],
    )
    registry = SourceRegistry([log_adapter])
    service = DefaultCollectionService(registry=registry)

    catalog = registry.capabilities(sample_incident)
    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-001",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-logs",
                source_type=SourceType.LOGS,
                question="Fetch recent log entries",
                parameters={"service": "checkout-service", "limit": 10},
            )
        ],
    )

    batch = await service.collect(plan, catalog)
    assert isinstance(batch, RawEvidenceBatch)
    assert batch.incident_id == sample_incident.incident_id
    assert batch.plan_id == "plan-001"
    assert len(batch.results) == 1
    assert batch.results[0].query_id == "q-logs"
    assert batch.results[0].source_status == SourceStatus.OK
    assert len(batch.results[0].records) == 1
    assert batch.results[0].records[0].source_record_id == "rec-1"


@pytest.mark.asyncio
async def test_unknown_parameter_rejection(sample_incident: IncidentSeed):
    """Queries with unknown/unsupported parameters are rejected with StructuredError."""
    log_adapter = ScriptedSourceAdapter(
        source_type=SourceType.LOGS,
        adapter_name="test_logs",
        supported_fields=["service", "limit"],
    )
    registry = SourceRegistry([log_adapter])
    service = DefaultCollectionService(registry=registry)

    catalog = registry.capabilities(sample_incident)
    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-002",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-invalid-param",
                source_type=SourceType.LOGS,
                question="Query with invalid param",
                parameters={"service": "checkout-service", "unsupported_field_xyz": "value"},
            )
        ],
    )

    batch = await service.collect(plan, catalog)
    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.source_status == SourceStatus.ERROR
    assert len(result.records) == 0
    assert any("unsupported fields" in w for w in result.warnings)
    assert len(batch.errors) >= 1
    assert "unsupported fields" in batch.errors[0].message


@pytest.mark.asyncio
async def test_window_exceeded_rejection(sample_incident: IncidentSeed):
    """Queries exceeding the capability maximum window are rejected with StructuredError."""
    cap = SourceCapability(
        source_type=SourceType.METRICS,
        available=True,
        supported_query_fields=["service", "start_time", "end_time"],
        maximum_window_seconds=3600,
        maximum_items=100,
        adapter_name="prom",
    )
    prom_adapter = ScriptedSourceAdapter(
        source_type=SourceType.METRICS,
        adapter_name="prom",
    )
    registry = SourceRegistry([prom_adapter])
    service = DefaultCollectionService(registry=registry)

    now = datetime.now(timezone.utc)
    catalog = SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=now,
        sources=[cap],
    )

    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-003",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-wide-window",
                source_type=SourceType.METRICS,
                question="Query exceeding window",
                parameters={
                    "start_time": (now - timedelta(days=2)).isoformat(),
                    "end_time": now.isoformat(),
                },
            )
        ],
    )

    batch = await service.collect(plan, catalog)
    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.source_status == SourceStatus.ERROR
    assert any("exceeds maximum allowed" in w for w in result.warnings)
    assert len(batch.errors) >= 1


@pytest.mark.asyncio
async def test_bounded_concurrency(sample_incident: IncidentSeed):
    """Collection service bounds concurrent queries using max_concurrency semaphore."""
    active_concurrent = 0
    max_observed_concurrent = 0
    lock = asyncio.Lock()

    async def tracking_query(query: EvidenceQuery) -> SourceResult:
        nonlocal active_concurrent, max_observed_concurrent
        async with lock:
            active_concurrent += 1
            if active_concurrent > max_observed_concurrent:
                max_observed_concurrent = active_concurrent

        await asyncio.sleep(0.05)

        async with lock:
            active_concurrent -= 1

        now = datetime.now(timezone.utc)
        return SourceResult(
            query_id=query.query_id,
            source_type=query.source_type,
            source_adapter="slow_adapter",
            source_status=SourceStatus.OK,
            records=[],
            started_at=now,
            completed_at=now,
        )

    class CustomTrackingAdapter(ScriptedSourceAdapter):
        async def query(self, query: EvidenceQuery) -> SourceResult:
            return await tracking_query(query)

    adapter = CustomTrackingAdapter(
        source_type=SourceType.HEALTH,
        adapter_name="slow_adapter",
    )
    registry = SourceRegistry([adapter])
    service = DefaultCollectionService(registry=registry, max_concurrency=2)
    catalog = registry.capabilities(sample_incident)

    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-concurrency",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id=f"q-{i}",
                source_type=SourceType.HEALTH,
                question=f"Check health endpoint {i}",
            )
            for i in range(6)
        ],
    )

    batch = await service.collect(plan, catalog)
    assert len(batch.results) == 6
    assert max_observed_concurrent <= 2


@pytest.mark.asyncio
async def test_query_timeout_converts_to_structured_error(sample_incident: IncidentSeed):
    """When a query times out, it produces an explicit SourceResult and StructuredError."""
    slow_adapter = ScriptedSourceAdapter(
        source_type=SourceType.CHANGES,
        adapter_name="hanging_adapter",
        delay_seconds=1.0,
    )
    registry = SourceRegistry([slow_adapter])
    service = DefaultCollectionService(
        registry=registry,
        query_timeout_seconds=0.05,
    )
    catalog = registry.capabilities(sample_incident)

    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-timeout",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-hang",
                source_type=SourceType.CHANGES,
                question="Query that will hang",
            )
        ],
    )

    batch = await service.collect(plan, catalog)
    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.source_status == SourceStatus.TIMEOUT
    assert any("timed out" in w for w in result.warnings)
    assert len(batch.errors) >= 1
    assert "timed out" in batch.errors[0].message


@pytest.mark.asyncio
async def test_source_exception_converts_to_error_without_synthesizing_data(sample_incident: IncidentSeed):
    """When an adapter raises an unexpected exception, it converts to SourceResult with ERROR status."""
    crashing_adapter = ScriptedSourceAdapter(
        source_type=SourceType.DEPLOYMENTS,
        adapter_name="failing_adapter",
        fail_with_exception=ConnectionResetError("Socket abruptly closed"),
    )
    registry = SourceRegistry([crashing_adapter])
    service = DefaultCollectionService(registry=registry)
    catalog = registry.capabilities(sample_incident)

    plan = EvidenceQueryPlan(
        incident_id=sample_incident.incident_id,
        plan_id="plan-crash",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-crash",
                source_type=SourceType.DEPLOYMENTS,
                question="Query that crashes",
            )
        ],
    )

    batch = await service.collect(plan, catalog)
    assert len(batch.results) == 1
    result = batch.results[0]
    assert result.source_status == SourceStatus.ERROR
    assert len(result.records) == 0
    assert any("failed" in w for w in result.warnings)
    assert len(batch.errors) >= 1
    assert "Socket abruptly closed" in batch.errors[0].message
