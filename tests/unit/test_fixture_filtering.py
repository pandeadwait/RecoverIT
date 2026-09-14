"""Unit tests for query parameter filtering across all fixture source adapters.

Verifies requirements from CREDIBILITY_IMPROVEMENT_PLAN §3.2 (Phase 2):
- Fixture queries enforce parameters instead of returning all pre-recorded data.
- Filtering by service, time window, pattern, severity, metric_name, paths, status, key.
- Discarding unmatched records returns SourceStatus.OK with empty records and explicit warning.
- Empty parameters retain backward-compatible behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from collectors import (
    FixtureChangeAdapter,
    FixtureConfigurationAdapter,
    FixtureDeploymentAdapter,
    FixtureLogAdapter,
    FixtureMetricAdapter,
    FixturePipelineAdapter,
)
from collectors.interfaces import SourceQuery
from contracts.collection.batch import QueryResult
from contracts.enums import InformationValue, SourceStatus, SourceType


def _make_query(
    query_id: str,
    source_type: SourceType,
    parameters: dict | None = None,
) -> SourceQuery:
    return SourceQuery(
        query_id=query_id,
        source_type=source_type,
        question=f"Test query for {source_type.value}",
        parameters=parameters or {},
        expected_information_value=InformationValue.HIGH,
    )


class TestMetricFixtureFiltering:
    """Tests for metric fixture filtering (exact metric_name, service, time)."""

    def test_inquiry_for_nonexistent_metric_returns_zero_records(self):
        """metric_name='response_time' on scenario 1 should return 0 records."""
        adapter = FixtureMetricAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_met_01",
            SourceType.METRICS,
            parameters={"metric_name": "response_time"},
        )
        result = adapter.query(query)

        assert isinstance(result, QueryResult)
        assert result.source_status == SourceStatus.OK
        assert result.records == []
        assert result.truncated is False
        assert any(
            "No records matched the requested filters." in w for w in result.warnings
        )

    def test_inquiry_for_exact_metric_returns_matching_record(self):
        adapter = FixtureMetricAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_met_02",
            SourceType.METRICS,
            parameters={"metric_name": "http_500_rate"},
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 1
        assert result.records[0].payload["metric_name"] == "http_500_rate"

    def test_inquiry_with_unmatched_service_returns_zero_records(self):
        adapter = FixtureMetricAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_met_03",
            SourceType.METRICS,
            parameters={
                "metric_name": "http_500_rate",
                "service": "unrelated-service",
            },
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 0
        assert "No records matched the requested filters." in result.warnings


class TestLogFixtureFiltering:
    """Tests for log fixture filtering (pattern regex/substring, severity/level, service)."""

    def test_pattern_matching_connection_pool_returns_one_record(self):
        """pattern='connection pool' returns 1 record in scenario 1."""
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_log_01",
            SourceType.LOGS,
            parameters={"pattern": "connection pool"},
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 1
        assert result.records[0].source_record_id == "log-db-002"
        assert "Connection pool exhausted" in result.records[0].payload["message"]

    def test_pattern_matching_segmentation_fault_returns_zero_records(self):
        """pattern='segmentation fault' returns 0 records in scenario 1."""
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_log_02",
            SourceType.LOGS,
            parameters={"pattern": "segmentation fault"},
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 0
        assert "No records matched the requested filters." in result.warnings

    def test_severity_filter_distinguishes_levels(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")

        # In bad_db_config: log-db-001 is "error", log-db-002 is "critical"
        query_error = _make_query(
            "q_log_err",
            SourceType.LOGS,
            parameters={"severity": "error"},
        )
        res_error = adapter.query(query_error)
        assert len(res_error.records) == 1
        assert res_error.records[0].payload["level"] == "error"

        query_info = _make_query(
            "q_log_info",
            SourceType.LOGS,
            parameters={"severity": "info"},
        )
        res_info = adapter.query(query_info)
        assert len(res_info.records) == 0

    def test_severity_list_filtering(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_log_both",
            SourceType.LOGS,
            parameters={"severity": ["error", "critical"]},
        )
        result = adapter.query(query)
        assert len(result.records) == 2


class TestChangeFixtureFiltering:
    """Tests for change fixture filtering (paths against files_changed, service)."""

    def test_paths_unrelated_file_returns_zero_records(self):
        """paths=['unrelated/path.py'] returns 0 records in scenario 1."""
        adapter = FixtureChangeAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_chg_01",
            SourceType.CHANGES,
            parameters={"paths": ["unrelated/path.py"]},
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 0
        assert "No records matched the requested filters." in result.warnings

    def test_paths_matching_file_returns_commit(self):
        adapter = FixtureChangeAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_chg_02",
            SourceType.CHANGES,
            parameters={"paths": ["config/database.yaml"]},
        )
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 1
        assert result.records[0].source_record_id == "commit-c101"
        assert "config/database.yaml" in result.records[0].payload["files_changed"]


class TestDeploymentFixtureFiltering:
    """Tests for deployment fixture filtering (status, version/deployment_id, service)."""

    def test_status_filter(self):
        adapter = FixtureDeploymentAdapter(scenario_name="bad_db_config")

        query_ok = _make_query(
            "q_dep_ok",
            SourceType.DEPLOYMENTS,
            parameters={"status": "succeeded"},
        )
        res_ok = adapter.query(query_ok)
        assert len(res_ok.records) == 1

        query_fail = _make_query(
            "q_dep_fail",
            SourceType.DEPLOYMENTS,
            parameters={"status": "failed"},
        )
        res_fail = adapter.query(query_fail)
        assert len(res_fail.records) == 0

    def test_version_filter(self):
        adapter = FixtureDeploymentAdapter(scenario_name="bad_db_config")

        query_v = _make_query(
            "q_dep_v",
            SourceType.DEPLOYMENTS,
            parameters={"version": "v2.4.1"},
        )
        assert len(adapter.query(query_v).records) == 1

        query_other = _make_query(
            "q_dep_other",
            SourceType.DEPLOYMENTS,
            parameters={"version": "v9.9.9"},
        )
        assert len(adapter.query(query_other).records) == 0


class TestPipelineFixtureFiltering:
    """Tests for pipeline fixture filtering (pipeline name, status)."""

    def test_pipeline_name_matching(self):
        adapter = FixturePipelineAdapter(scenario_name="bad_db_config")

        query_match = _make_query(
            "q_pipe_m",
            SourceType.PIPELINES,
            parameters={"pipeline": "payment-api-ci"},
        )
        assert len(adapter.query(query_match).records) == 1

        query_nomatch = _make_query(
            "q_pipe_no",
            SourceType.PIPELINES,
            parameters={"pipeline": "unrelated-ci"},
        )
        assert len(adapter.query(query_nomatch).records) == 0


class TestConfigurationFixtureFiltering:
    """Tests for configuration fixture filtering (key/keys)."""

    def test_key_matching(self):
        adapter = FixtureConfigurationAdapter(scenario_name="bad_db_config")

        query_match = _make_query(
            "q_cfg_m",
            SourceType.CONFIGURATION,
            parameters={"key": "database.host"},
        )
        assert len(adapter.query(query_match).records) == 1
        assert adapter.query(query_match).records[0].payload["key"] == "database.host"

        query_nomatch = _make_query(
            "q_cfg_no",
            SourceType.CONFIGURATION,
            parameters={"keys": ["redis.port", "redis.host"]},
        )
        assert len(adapter.query(query_nomatch).records) == 0


class TestTimeWindowFiltering:
    """Tests for time window bounds (start_time, end_time)."""

    def test_window_enclosing_events(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        # Logs occurred at 10:27:15 and 10:28:00
        query = _make_query(
            "q_time_enclosing",
            SourceType.LOGS,
            parameters={
                "start_time": "2026-09-12T10:25:00Z",
                "end_time": "2026-09-12T10:30:00Z",
            },
        )
        result = adapter.query(query)
        assert len(result.records) == 2

    def test_window_in_past_returns_zero(self):
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_time_past",
            SourceType.LOGS,
            parameters={
                "start_time": "2026-09-01T00:00:00Z",
                "end_time": "2026-09-01T01:00:00Z",
            },
        )
        result = adapter.query(query)
        assert len(result.records) == 0
        assert "No records matched the requested filters." in result.warnings


class TestEmptyResultContract:
    """Tests verifying the contract when zero records match."""

    def test_empty_results_maintain_valid_contract_shape(self):
        adapter = FixtureMetricAdapter(scenario_name="bad_db_config")
        query = _make_query(
            "q_empty_contract",
            SourceType.METRICS,
            parameters={"metric_name": "non_existent_metric"},
        )
        result = adapter.query(query)

        assert isinstance(result, QueryResult)
        assert result.query_id == "q_empty_contract"
        assert result.source_type == SourceType.METRICS
        assert result.source_adapter == adapter.adapter_name
        assert result.source_status == SourceStatus.OK
        assert result.truncated is False
        assert result.records == []
        assert isinstance(result.warnings, list)
        assert len(result.warnings) > 0
        assert "No records matched the requested filters." in result.warnings

    def test_unparameterized_query_returns_all_records(self):
        """Unparameterized query returns all records (preserves backward compatibility)."""
        adapter = FixtureLogAdapter(scenario_name="bad_db_config")
        query = _make_query("q_unparam", SourceType.LOGS, parameters={})
        result = adapter.query(query)

        assert result.source_status == SourceStatus.OK
        assert len(result.records) == 2
        assert result.warnings == []
