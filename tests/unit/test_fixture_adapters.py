"""Tests for Fixture Adapters, Scenarios, and Replay Adapter (Phase 4).

Verifies requirements from WORK_DIVISION §6.2, §6.6, §6.8, §6.9,
and implementation_plan.md Phase 4.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from collectors import (
    BaseSource,
    ChangeSource,
    ConfigurationSource,
    DeploymentSource,
    FixtureChangeAdapter,
    FixtureConfigurationAdapter,
    FixtureDeploymentAdapter,
    FixtureLogAdapter,
    FixtureMetricAdapter,
    FixturePipelineAdapter,
    LogSource,
    MetricSource,
    PipelineSource,
    ReplayAdapter,
    create_scenario_adapters,
    list_available_scenarios,
    load_scenario_json,
    load_scenario_records,
)
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import InformationValue, SourceStatus, SourceType

NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)


def _make_query(
    query_id: str = "qry_001",
    source_type: SourceType = SourceType.LOGS,
    parameters: dict | None = None,
) -> EvidenceQuery:
    return EvidenceQuery(
        query_id=query_id,
        source_type=source_type,
        question="What happened?",
        parameters=parameters or {},
        expected_information_value=InformationValue.HIGH,
    )


# ── Scenario Data Files Completeness ────────────────────────────────


class TestScenarioDataFiles:
    def test_five_scenario_families_exist(self):
        scenarios = list_available_scenarios()
        expected = [
            "bad_db_config",
            "memory_exhaustion",
            "dependency_incompatibility",
            "real_db_outage",
            "coincidental_deployment",
        ]
        assert set(expected).issubset(set(scenarios))

    @pytest.mark.parametrize(
        "scenario_name",
        [
            "bad_db_config",
            "memory_exhaustion",
            "dependency_incompatibility",
            "real_db_outage",
            "coincidental_deployment",
        ],
    )
    def test_all_six_source_types_represented_in_scenario(
        self, scenario_name: str
    ):
        data = load_scenario_json(scenario_name)
        sources = data.get("sources", {})
        for st in SourceType:
            assert st.value in sources, f"Source {st.value} missing in {scenario_name}"
            records = load_scenario_records(scenario_name, st)
            assert isinstance(records, list)
            assert len(records) > 0, f"Source {st.value} has 0 records in {scenario_name}"

    @pytest.mark.parametrize(
        "scenario_name",
        [
            "bad_db_config",
            "memory_exhaustion",
            "dependency_incompatibility",
            "real_db_outage",
            "coincidental_deployment",
        ],
    )
    def test_no_credential_like_data_in_fixtures(self, scenario_name: str):
        """Ensure no secrets or tokens exist in test data (Principle 4.8, §6.7)."""
        raw_json = json.dumps(load_scenario_json(scenario_name)).lower()
        forbidden_patterns = [
            "bearer eyj",
            "api_key=",
            "password123",
            "-----begin rsa private key-----",
            "aws_secret_access_key",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in raw_json


# ── Six Individual Fixture Adapters ─────────────────────────────────


class TestFixtureAdapters:
    def test_fixture_log_adapter(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, LogSource)
        assert isinstance(adapter, BaseSource)
        assert adapter.source_type == SourceType.LOGS

        query = _make_query("q_log", SourceType.LOGS)
        result = adapter.query(query)

        assert isinstance(result, QueryResult)
        assert result.query_id == "q_log"
        assert result.source_type == SourceType.LOGS
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0
        assert result.truncated is False

    def test_fixture_metric_adapter(self):
        adapter = FixtureMetricAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, MetricSource)
        assert adapter.source_type == SourceType.METRICS

        query = _make_query("q_met", SourceType.METRICS)
        result = adapter.query(query)
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0

    def test_fixture_change_adapter(self):
        adapter = FixtureChangeAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, ChangeSource)
        assert adapter.source_type == SourceType.CHANGES

        query = _make_query("q_chg", SourceType.CHANGES)
        result = adapter.query(query)
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0

    def test_fixture_deployment_adapter(self):
        adapter = FixtureDeploymentAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, DeploymentSource)
        assert adapter.source_type == SourceType.DEPLOYMENTS

        query = _make_query("q_dep", SourceType.DEPLOYMENTS)
        result = adapter.query(query)
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0

    def test_fixture_pipeline_adapter(self):
        adapter = FixturePipelineAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, PipelineSource)
        assert adapter.source_type == SourceType.PIPELINES

        query = _make_query("q_pip", SourceType.PIPELINES)
        result = adapter.query(query)
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0

    def test_fixture_configuration_adapter(self):
        adapter = FixtureConfigurationAdapter(scenario_name="bad_db_config")
        assert isinstance(adapter, ConfigurationSource)
        assert adapter.source_type == SourceType.CONFIGURATION

        query = _make_query("q_cfg", SourceType.CONFIGURATION)
        result = adapter.query(query)
        assert result.source_status == SourceStatus.OK
        assert len(result.records) > 0


# ── Limits & Truncation ─────────────────────────────────────────────


class TestAdapterLimitsAndTruncation:
    def test_limit_truncates_records_and_sets_flag(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        # Scenario 1 has 2 logs; request limit of 1
        query = _make_query("q_limit", SourceType.LOGS, parameters={"limit": 1})
        result = adapter.query(query)

        assert result.truncated is True
        assert len(result.records) == 1
        assert any("truncated" in w.lower() for w in result.warnings)

    def test_limit_larger_than_record_count_not_truncated(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query("q_nolimit", SourceType.LOGS, parameters={"limit": 100})
        result = adapter.query(query)

        assert result.truncated is False
        assert len(result.records) == 2


# ── Simulated Error & Failure States ────────────────────────────────


class TestAdapterFailureModes:
    def test_source_unavailable_status(self):
        adapter = FixtureMetricAdapter(
            scenario_name="bad_db_config",
            simulated_status=SourceStatus.UNAVAILABLE,
        )
        result = adapter.query(_make_query("q_unavail", SourceType.METRICS))

        assert result.source_status == SourceStatus.UNAVAILABLE
        assert result.records == []
        assert len(result.warnings) > 0

    def test_source_timeout_status(self):
        adapter = FixtureLogAdapter(
            scenario_name="bad_db_config",
            simulated_status=SourceStatus.TIMEOUT,
        )
        result = adapter.query(_make_query("q_timeout", SourceType.LOGS))

        assert result.source_status == SourceStatus.TIMEOUT
        assert result.records == []
        assert any("timed out" in w.lower() or "timeout" in w.lower() for w in result.warnings)

    def test_source_error_status(self):
        adapter = FixtureChangeAdapter(
            scenario_name="bad_db_config",
            simulated_status=SourceStatus.ERROR,
            simulated_error="Git repository locked by another process",
        )
        result = adapter.query(_make_query("q_err", SourceType.CHANGES))

        assert result.source_status == SourceStatus.ERROR
        assert result.records == []
        assert "Git repository locked" in result.warnings[0]


# ── Scenario Factory Function ───────────────────────────────────────


class TestScenarioFactory:
    def test_create_scenario_adapters_returns_all_six(self):
        adapters = create_scenario_adapters("memory_exhaustion")
        assert len(adapters) == 6
        for st in SourceType:
            assert st in adapters
            assert adapters[st].source_type == st
            res = adapters[st].query(_make_query("q_factory", st))
            assert res.source_status == SourceStatus.OK
            assert len(res.records) > 0


# ── Replay Adapter ──────────────────────────────────────────────────


class TestReplayAdapter:
    def test_replay_returns_deterministic_recorded_result(self):
        recorded_res = QueryResult(
            query_id="qry_recorded_1",
            source_type=SourceType.LOGS,
            source_adapter="test-recorder",
            source_status=SourceStatus.OK,
            truncated=False,
            records=[
                RawRecord(
                    source_record_id="rec-001",
                    event_time=NOW,
                    content_type="application_log",
                    payload={"message": "Recorded line 1"},
                )
            ],
            warnings=[],
        )

        replay = ReplayAdapter(
            source_type=SourceType.LOGS,
            recorded_results={"qry_recorded_1": recorded_res},
        )

        # Call twice to verify deterministic replay
        res1 = replay.query(_make_query("qry_recorded_1", SourceType.LOGS))
        res2 = replay.query(_make_query("qry_recorded_1", SourceType.LOGS))

        assert res1 == recorded_res
        assert res2 == recorded_res
        assert res1.model_dump_json() == res2.model_dump_json()

    def test_replay_unknown_query_returns_unavailable_with_warning(self):
        replay = ReplayAdapter(source_type=SourceType.LOGS)
        res = replay.query(_make_query("unknown_query_id", SourceType.LOGS))

        assert res.source_status == SourceStatus.UNAVAILABLE
        assert any("no recorded result" in w.lower() for w in res.warnings)


# ── Schema Validation Round-Trip ────────────────────────────────────


class TestSchemaRoundTrip:
    def test_adapter_output_validates_against_query_result_schema(self):
        for scenario in list_available_scenarios():
            adapters = create_scenario_adapters(scenario)
            for st, adapter in adapters.items():
                res = adapter.query(_make_query(f"q_{st.value}", st))
                json_str = res.model_dump_json()
                restored = QueryResult.model_validate_json(json_str)
                assert restored == res
