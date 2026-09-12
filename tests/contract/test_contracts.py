"""Contract round-trip and validation tests for all shared schemas.

Covers Phase 1 deliverables:
- All contracts round-trip through JSON serialization.
- Required fields are enforced.
- schema_version is present on every contract.
- Enumerations reject unknown values.
- Deterministic JSON output for hashing and replay.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from contracts.enums import (
    InformationValue,
    Severity,
    SourceCoverage,
    SourceStatus,
    SourceType,
)
from contracts.errors import StructuredError
from contracts.incident.alert import IncidentAlert
from contracts.incident.seed import IncidentSeed
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.collection.batch import QueryResult, RawEvidenceBatch, RawRecord


# ── Helpers ─────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(seconds=2)


def _valid_alert_data() -> dict:
    return {
        "external_alert_id": "alert-001",
        "service": "payment-api",
        "environment": "simulation",
        "severity": "critical",
        "detected_at": "2026-09-12T10:30:00Z",
        "message": "HTTP 500 rate exceeded threshold",
        "labels": {"region": "local"},
    }


# ── Enumerations ────────────────────────────────────────────────────


class TestSeverity:
    def test_accepted_values(self):
        assert Severity("info") is Severity.INFO
        assert Severity("warning") is Severity.WARNING
        assert Severity("critical") is Severity.CRITICAL

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            Severity("panic")


class TestSourceType:
    def test_all_six_present(self):
        expected = {"logs", "metrics", "changes", "deployments", "pipelines", "configuration"}
        actual = {st.value for st in SourceType}
        assert actual == expected

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            SourceType("database")


class TestSourceStatus:
    def test_accepted_values(self):
        for val in ("ok", "partial", "unavailable", "timeout", "error"):
            assert SourceStatus(val)

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            SourceStatus("crashed")


class TestSourceCoverage:
    def test_accepted_values(self):
        for val in ("available", "not_queried", "empty", "unavailable"):
            assert SourceCoverage(val)

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            SourceCoverage("partial")


class TestInformationValue:
    def test_accepted_values(self):
        for val in ("high", "medium", "low"):
            assert InformationValue(val)

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            InformationValue("extreme")


# ── StructuredError ─────────────────────────────────────────────────


class TestStructuredError:
    def test_roundtrip(self):
        err = StructuredError(
            code="SOURCE_UNAVAILABLE",
            message="The metrics source did not respond before the deadline.",
            retryable=True,
            source="metrics",
            details={"query_id": "qry_123"},
        )
        json_str = err.model_dump_json()
        parsed = StructuredError.model_validate_json(json_str)
        assert parsed == err

    def test_schema_version_present(self):
        err = StructuredError(code="TEST", message="test")
        data = json.loads(err.model_dump_json())
        assert data["schema_version"] == "1.0"

    def test_defaults(self):
        err = StructuredError(code="X", message="y")
        assert err.retryable is False
        assert err.source is None
        assert err.details == {}

    def test_missing_required_code(self):
        with pytest.raises(ValidationError):
            StructuredError(message="oops")  # type: ignore[call-arg]

    def test_missing_required_message(self):
        with pytest.raises(ValidationError):
            StructuredError(code="ERR")  # type: ignore[call-arg]


# ── IncidentAlert ───────────────────────────────────────────────────


class TestIncidentAlert:
    def test_valid_roundtrip(self):
        alert = IncidentAlert(**_valid_alert_data())
        json_str = alert.model_dump_json()
        parsed = IncidentAlert.model_validate_json(json_str)
        assert parsed == alert

    def test_schema_version_present(self):
        alert = IncidentAlert(**_valid_alert_data())
        data = json.loads(alert.model_dump_json())
        assert data["schema_version"] == "1.0"

    @pytest.mark.parametrize(
        "missing_field",
        ["external_alert_id", "service", "environment", "severity", "detected_at", "message"],
    )
    def test_missing_required_field(self, missing_field: str):
        data = _valid_alert_data()
        del data[missing_field]
        with pytest.raises(ValidationError):
            IncidentAlert(**data)

    def test_invalid_severity(self):
        data = _valid_alert_data()
        data["severity"] = "panic"
        with pytest.raises(ValidationError):
            IncidentAlert(**data)

    def test_detected_at_without_timezone_rejected(self):
        data = _valid_alert_data()
        data["detected_at"] = "2026-09-12T10:30:00"  # naive — no tz
        with pytest.raises(ValidationError):
            IncidentAlert(**data)

    def test_labels_default_empty(self):
        data = _valid_alert_data()
        del data["labels"]
        alert = IncidentAlert(**data)
        assert alert.labels == {}

    def test_labels_non_string_value_rejected(self):
        data = _valid_alert_data()
        data["labels"] = {"key": 123}
        with pytest.raises(ValidationError):
            IncidentAlert(**data)

    def test_deterministic_json(self):
        """Same input must produce identical JSON for hashing."""
        data = _valid_alert_data()
        a = IncidentAlert(**data).model_dump_json()
        b = IncidentAlert(**data).model_dump_json()
        assert a == b


# ── IncidentSeed ────────────────────────────────────────────────────


class TestIncidentSeed:
    def test_valid_roundtrip(self):
        seed = IncidentSeed(
            incident_id="inc_001",
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=NOW,
            received_at=LATER,
            summary="HTTP 500 rate exceeded threshold",
            labels={"region": "local"},
        )
        json_str = seed.model_dump_json()
        parsed = IncidentSeed.model_validate_json(json_str)
        assert parsed == seed

    def test_schema_version_present(self):
        seed = IncidentSeed(
            incident_id="inc_001",
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=NOW,
            received_at=LATER,
            summary="test",
        )
        data = json.loads(seed.model_dump_json())
        assert data["schema_version"] == "1.0"

    def test_missing_incident_id_rejected(self):
        with pytest.raises(ValidationError):
            IncidentSeed(
                external_alert_id="alert-001",
                service="payment-api",
                environment="simulation",
                severity=Severity.CRITICAL,
                detected_at=NOW,
                received_at=LATER,
                summary="test",
            )  # type: ignore[call-arg]

    def test_deterministic_json(self):
        kwargs = dict(
            incident_id="inc_001",
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=NOW,
            received_at=LATER,
            summary="test",
        )
        a = IncidentSeed(**kwargs).model_dump_json()
        b = IncidentSeed(**kwargs).model_dump_json()
        assert a == b


# ── SourceCapabilityCatalog ─────────────────────────────────────────


class TestSourceCapabilityCatalog:
    def test_valid_roundtrip(self):
        catalog = SourceCapabilityCatalog(
            incident_id="inc_001",
            generated_at=NOW,
            sources=[
                SourceCapability(
                    source_type=SourceType.LOGS,
                    available=True,
                    supported_query_fields=[
                        "service", "start_time", "end_time", "severity", "pattern", "limit",
                    ],
                    maximum_window_seconds=86400,
                    maximum_items=1000,
                ),
                SourceCapability(
                    source_type=SourceType.METRICS,
                    available=False,
                    supported_query_fields=["service", "metric_name", "start_time", "end_time"],
                    maximum_window_seconds=86400,
                    maximum_items=500,
                ),
            ],
        )
        json_str = catalog.model_dump_json()
        parsed = SourceCapabilityCatalog.model_validate_json(json_str)
        assert parsed == catalog
        assert len(parsed.sources) == 2

    def test_schema_version_present(self):
        catalog = SourceCapabilityCatalog(
            incident_id="inc_001", generated_at=NOW, sources=[]
        )
        data = json.loads(catalog.model_dump_json())
        assert data["schema_version"] == "1.0"

    def test_empty_sources_allowed(self):
        catalog = SourceCapabilityCatalog(
            incident_id="inc_001", generated_at=NOW, sources=[]
        )
        assert catalog.sources == []


# ── EvidenceQueryPlan ───────────────────────────────────────────────


class TestEvidenceQueryPlan:
    def test_valid_roundtrip(self):
        plan = EvidenceQueryPlan(
            incident_id="inc_001",
            plan_id="plan_002",
            round=2,
            queries=[
                EvidenceQuery(
                    query_id="qry_101",
                    source_type=SourceType.LOGS,
                    question="Which errors appeared immediately after deployment?",
                    parameters={
                        "service": "payment-api",
                        "start_time": "2026-09-12T10:20:00Z",
                        "end_time": "2026-09-12T10:35:00Z",
                        "severity": ["error", "critical"],
                        "limit": 200,
                    },
                    related_information_ids=["need_02"],
                    discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                    expected_information_value=InformationValue.HIGH,
                ),
            ],
            stop_reason=None,
        )
        json_str = plan.model_dump_json()
        parsed = EvidenceQueryPlan.model_validate_json(json_str)
        assert parsed == plan

    def test_schema_version_present(self):
        plan = EvidenceQueryPlan(
            incident_id="inc_001", plan_id="plan_001", round=1, queries=[]
        )
        data = json.loads(plan.model_dump_json())
        assert data["schema_version"] == "1.0"

    def test_stop_reason_with_empty_queries(self):
        plan = EvidenceQueryPlan(
            incident_id="inc_001",
            plan_id="plan_003",
            round=3,
            queries=[],
            stop_reason="budget_exhausted",
        )
        assert plan.stop_reason == "budget_exhausted"
        assert plan.queries == []


# ── RawEvidenceBatch ────────────────────────────────────────────────


class TestRawEvidenceBatch:
    def test_valid_roundtrip(self):
        batch = RawEvidenceBatch(
            incident_id="inc_001",
            plan_id="plan_002",
            batch_id="batch_009",
            collected_at=NOW,
            results=[
                QueryResult(
                    query_id="qry_101",
                    source_type=SourceType.LOGS,
                    source_adapter="fixture-log-adapter",
                    source_status=SourceStatus.OK,
                    truncated=False,
                    records=[
                        RawRecord(
                            source_record_id="log-998",
                            event_time=NOW - timedelta(minutes=3),
                            observed_at=NOW - timedelta(minutes=3, seconds=-1),
                            content_type="application_log",
                            payload={
                                "level": "error",
                                "message": "Database connection timeout",
                                "service": "payment-api",
                            },
                        ),
                    ],
                    warnings=[],
                ),
            ],
            errors=[],
        )
        json_str = batch.model_dump_json()
        parsed = RawEvidenceBatch.model_validate_json(json_str)
        assert parsed == batch
        assert len(parsed.results) == 1
        assert len(parsed.results[0].records) == 1

    def test_schema_version_present(self):
        batch = RawEvidenceBatch(
            incident_id="inc_001",
            plan_id="plan_001",
            batch_id="batch_001",
            collected_at=NOW,
        )
        data = json.loads(batch.model_dump_json())
        assert data["schema_version"] == "1.0"

    def test_batch_with_errors(self):
        batch = RawEvidenceBatch(
            incident_id="inc_001",
            plan_id="plan_002",
            batch_id="batch_010",
            collected_at=NOW,
            results=[],
            errors=[
                StructuredError(
                    code="SOURCE_UNAVAILABLE",
                    message="Metrics source timed out.",
                    retryable=True,
                    source="metrics",
                ),
            ],
        )
        json_str = batch.model_dump_json()
        parsed = RawEvidenceBatch.model_validate_json(json_str)
        assert parsed == batch
        assert len(parsed.errors) == 1
        assert parsed.errors[0].code == "SOURCE_UNAVAILABLE"

    def test_record_optional_timestamps(self):
        record = RawRecord(
            source_record_id="rec-001",
            content_type="configuration_change",
            payload={"key": "db_host", "old": "localhost", "new": "db.prod"},
        )
        assert record.event_time is None
        assert record.observed_at is None

    def test_deterministic_json(self):
        kwargs = dict(
            incident_id="inc_001",
            plan_id="plan_001",
            batch_id="batch_001",
            collected_at=NOW,
            results=[
                QueryResult(
                    query_id="qry_001",
                    source_type=SourceType.DEPLOYMENTS,
                    source_adapter="fixture-deploy-adapter",
                    source_status=SourceStatus.OK,
                    records=[
                        RawRecord(
                            source_record_id="dep-1",
                            event_time=NOW,
                            content_type="deployment_event",
                            payload={"version": "v2.4.1"},
                        ),
                    ],
                ),
            ],
        )
        a = RawEvidenceBatch(**kwargs).model_dump_json()
        b = RawEvidenceBatch(**kwargs).model_dump_json()
        assert a == b
