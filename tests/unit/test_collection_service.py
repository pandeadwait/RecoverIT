"""Tests for CollectionService, Query Validation, and RawEvidenceBatch assembly (Phase 5).

Verifies requirements from WORK_DIVISION §6.2, §6.4, §6.5, §6.6, §6.7, §6.9,
and implementation_plan.md Phase 5.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from collectors import (
    CollectionService,
    DefaultCollectionService,
    create_scenario_adapters,
)
from collectors.gateway.collection_service import validate_query
from contracts.collection.batch import QueryResult, RawEvidenceBatch
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.enums import (
    InformationValue,
    Severity,
    SourceStatus,
    SourceType,
)
from contracts.errors import StructuredError
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import FrozenClock
from ingestion.alert.id_gen import DeterministicIdentifierGenerator
from ingestion.capabilities.registry import (
    DefaultSourceRegistry,
    get_default_capability,
)

FIXED_TIME = datetime(2026, 9, 12, 10, 31, 0, tzinfo=timezone.utc)


def _setup_service_with_scenario(
    scenario_name: str = "bad_db_config",
    clock: FrozenClock | None = None,
    id_gen: DeterministicIdentifierGenerator | None = None,
) -> tuple[DefaultCollectionService, DefaultSourceRegistry]:
    clk = clock or FrozenClock(FIXED_TIME)
    gen = id_gen or DeterministicIdentifierGenerator(prefix="batch", start=1)
    registry = DefaultSourceRegistry(clock=clk)
    adapters = create_scenario_adapters(scenario_name)
    for adapter in adapters.values():
        registry.register_source(adapter)

    service = DefaultCollectionService(
        registry=registry,
        clock=clk,
        id_generator=gen,
        max_workers=4,
        timeout_seconds=5.0,
    )
    return service, registry


def _make_sample_plan(
    queries: list[EvidenceQuery] | None = None,
    incident_id: str = "inc_001",
    plan_id: str = "plan_002",
    round_num: int = 1,
    stop_reason: str | None = None,
) -> EvidenceQueryPlan:
    if queries is None:
        queries = [
            EvidenceQuery(
                query_id="qry_log_01",
                source_type=SourceType.LOGS,
                question="Which errors appeared?",
                parameters={
                    "service": "payment-api",
                    "start_time": "2026-09-12T10:20:00Z",
                    "end_time": "2026-09-12T10:35:00Z",
                    "severity": ["error", "critical"],
                    "limit": 50,
                },
                expected_information_value=InformationValue.HIGH,
            )
        ]
    return EvidenceQueryPlan(
        schema_version="1.0",
        incident_id=incident_id,
        plan_id=plan_id,
        round=round_num,
        queries=queries,
        stop_reason=stop_reason,
    )


# ── Successful Query Execution & Batch Assembly ─────────────────────


class TestCollectionServiceExecution:
    def test_valid_query_plan_produces_raw_evidence_batch(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        plan = _make_sample_plan()

        batch = service.execute(plan)

        assert isinstance(batch, RawEvidenceBatch)
        assert batch.schema_version == "1.0"
        assert batch.incident_id == "inc_001"
        assert batch.plan_id == "plan_002"
        assert batch.batch_id == "batch_001"
        assert batch.collected_at == FIXED_TIME
        assert len(batch.results) == 1
        assert batch.errors == []

        res = batch.results[0]
        assert res.query_id == "qry_log_01"
        assert res.source_type == SourceType.LOGS
        assert res.source_status == SourceStatus.OK
        assert len(res.records) > 0

    def test_all_six_source_categories_in_one_batch(self):
        """Verify queries across all six source categories can be executed together."""
        service, _ = _setup_service_with_scenario("bad_db_config")
        param_map = {
            SourceType.LOGS: {"limit": 10},
            SourceType.METRICS: {"metric_name": "http_500_rate"},
            SourceType.CHANGES: {"max_commits": 10},
            SourceType.DEPLOYMENTS: {"limit": 10},
            SourceType.PIPELINES: {"limit": 10},
            SourceType.CONFIGURATION: {"keys": ["database.host"]},
        }
        queries = [
            EvidenceQuery(
                query_id=f"qry_{st.value}",
                source_type=st,
                question=f"Query {st.value}",
                parameters=param_map[st],
                expected_information_value=InformationValue.MEDIUM,
            )
            for st in SourceType
        ]
        plan = _make_sample_plan(queries=queries)

        batch = service.execute(plan)

        assert len(batch.results) == 6
        assert batch.errors == []
        returned_sources = {r.source_type for r in batch.results}
        assert returned_sources == set(SourceType)

        for res in batch.results:
            assert res.source_status == SourceStatus.OK
            assert len(res.records) > 0

    def test_concurrent_execution_order_preserved_deterministically(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        queries = [
            EvidenceQuery(
                query_id=f"qry_{i:02d}",
                source_type=SourceType.LOGS,
                question=f"Concurrent query {i}",
                parameters={"limit": 5},
                expected_information_value=InformationValue.LOW,
            )
            for i in range(8, 0, -1)  # Submitted in reverse order
        ]
        plan = _make_sample_plan(queries=queries)

        batch = service.execute(plan)

        assert len(batch.results) == 8
        # Results must be sorted deterministically by query_id
        sorted_ids = [r.query_id for r in batch.results]
        assert sorted_ids == sorted(sorted_ids)

    def test_empty_query_plan_returns_empty_batch(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        plan = _make_sample_plan(queries=[], stop_reason="budget_exhausted")

        batch = service.execute(plan)

        assert batch.results == []
        assert batch.errors == []
        assert batch.batch_id == "batch_001"


# ── Query Parameter Validation Against Catalog ──────────────────────


class TestQueryValidationRules:
    def test_unsupported_query_fields_produce_structured_error(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        query = EvidenceQuery(
            query_id="qry_bad_param",
            source_type=SourceType.LOGS,
            question="Query with invalid field",
            parameters={
                "service": "payment-api",
                "malicious_injected_field": "DROP TABLE",
            },
            expected_information_value=InformationValue.LOW,
        )
        plan = _make_sample_plan(queries=[query])

        batch = service.execute(plan)

        assert len(batch.errors) > 0
        err = next(e for e in batch.errors if e.code == "INVALID_QUERY_FIELDS")
        assert err.retryable is False
        assert "malicious_injected_field" in str(err.details)
        assert batch.results[0].source_status == SourceStatus.ERROR

    def test_time_window_exceeding_maximum_window_rejected(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        # Logs maximum window is 86400s (24h). Request 72h window.
        query = EvidenceQuery(
            query_id="qry_wide_window",
            source_type=SourceType.LOGS,
            question="Too wide window",
            parameters={
                "start_time": "2026-09-09T10:00:00Z",
                "end_time": "2026-09-12T10:00:00Z",  # 72 hours
            },
            expected_information_value=InformationValue.LOW,
        )
        plan = _make_sample_plan(queries=[query])

        batch = service.execute(plan)

        err = next((e for e in batch.errors if e.code == "QUERY_WINDOW_EXCEEDED"), None)
        assert err is not None
        assert err.retryable is False
        assert err.details["window_seconds"] > 86400

    def test_query_against_unavailable_source_produces_error(self):
        # Empty registry: all sources unavailable
        registry = DefaultSourceRegistry()
        service = DefaultCollectionService(registry=registry)

        query = EvidenceQuery(
            query_id="qry_unavail",
            source_type=SourceType.METRICS,
            question="Query missing metrics",
            parameters={"service": "payment-api"},
            expected_information_value=InformationValue.MEDIUM,
        )
        plan = _make_sample_plan(queries=[query])

        batch = service.execute(plan)

        assert len(batch.errors) > 0
        err = next(e for e in batch.errors if e.code == "SOURCE_UNAVAILABLE")
        assert err.retryable is True
        assert err.source == "metrics"
        assert batch.results[0].source_status == SourceStatus.UNAVAILABLE


# ── Resilience & Partial Failure Handling ───────────────────────────


class TestResilienceAndFailureHandling:
    def test_one_source_timeout_does_not_crash_other_queries(self):
        """Partial failures must not crash the collection service (WORK_DIVISION §6.7, §6.10)."""
        from collectors.logs.fixture_adapter import FixtureLogAdapter

        clock = FrozenClock(FIXED_TIME)
        registry = DefaultSourceRegistry(clock=clock)
        # Register normal metrics adapter
        scenario_adapters = create_scenario_adapters("bad_db_config")
        registry.register_source(scenario_adapters[SourceType.METRICS])
        # Register a log adapter that simulates timeout
        timeout_log_adapter = FixtureLogAdapter(
            scenario_name="bad_db_config",
            simulated_status=SourceStatus.TIMEOUT,
        )
        registry.register_source(timeout_log_adapter)

        service = DefaultCollectionService(registry=registry, clock=clock)

        queries = [
            EvidenceQuery(
                query_id="q_good_metrics",
                source_type=SourceType.METRICS,
                question="Metrics query",
                parameters={},
                expected_information_value=InformationValue.HIGH,
            ),
            EvidenceQuery(
                query_id="q_timed_out_logs",
                source_type=SourceType.LOGS,
                question="Logs query",
                parameters={},
                expected_information_value=InformationValue.HIGH,
            ),
        ]
        plan = _make_sample_plan(queries=queries)

        batch = service.execute(plan)

        assert len(batch.results) == 2
        by_qid = {r.query_id: r for r in batch.results}

        # Metrics succeeded
        assert by_qid["q_good_metrics"].source_status == SourceStatus.OK
        assert len(by_qid["q_good_metrics"].records) > 0

        # Logs timed out cleanly without crashing the batch
        assert by_qid["q_timed_out_logs"].source_status == SourceStatus.TIMEOUT
        assert by_qid["q_timed_out_logs"].records == []

        # Timeout reflected in errors
        timeout_err = next((e for e in batch.errors if e.code == "SOURCE_TIMEOUT"), None)
        assert timeout_err is not None
        assert timeout_err.retryable is True

    def test_unhandled_adapter_exception_handled_gracefully(self):
        class BrokenAdapter:
            source_type = SourceType.PIPELINES
            adapter_name = "broken-adapter"

            def get_capability(self, incident=None):
                return get_default_capability(SourceType.PIPELINES)

            def query(self, query):
                raise RuntimeError("Catastrophic connection failure in adapter")

        registry = DefaultSourceRegistry()
        registry.register_source(BrokenAdapter())
        service = DefaultCollectionService(registry=registry)

        query = EvidenceQuery(
            query_id="q_broken",
            source_type=SourceType.PIPELINES,
            question="Broken query",
            parameters={},
            expected_information_value=InformationValue.LOW,
        )
        plan = _make_sample_plan(queries=[query])

        batch = service.execute(plan)

        assert len(batch.results) == 1
        assert batch.results[0].source_status == SourceStatus.ERROR
        assert "Catastrophic connection failure" in batch.results[0].warnings[0]

        err = next((e for e in batch.errors if e.code == "SOURCE_ERROR"), None)
        assert err is not None
        assert err.retryable is False


# ── Security & Schema Compliance ────────────────────────────────────


class TestSecurityAndSchemaCompliance:
    def test_no_credentials_in_raw_evidence_batch(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        queries = [
            EvidenceQuery(
                query_id=f"q_{st.value}",
                source_type=st,
                question="q",
                parameters={},
                expected_information_value=InformationValue.LOW,
            )
            for st in SourceType
        ]
        plan = _make_sample_plan(queries=queries)
        batch = service.execute(plan)

        batch_json = batch.model_dump_json().lower()
        forbidden_substrings = [
            "password",
            "bearer eyj",
            "private_key",
            "secret_access_key",
        ]
        for s in forbidden_substrings:
            assert s not in batch_json

    def test_batch_roundtrips_through_json_schema(self):
        service, _ = _setup_service_with_scenario("bad_db_config")
        plan = _make_sample_plan()

        batch = service.execute(plan)
        json_str = batch.model_dump_json()

        restored = RawEvidenceBatch.model_validate_json(json_str)
        assert restored == batch
