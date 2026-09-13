"""Tests for Source Capability Discovery and SourceRegistry (Phase 3).

Verifies requirements from WORK_DIVISION §6.5, §6.6, §6.8, §6.10,
and implementation_plan.md Phase 3.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from collectors.interfaces import (
    BaseSource,
    ChangeSource,
    ConfigurationSource,
    DeploymentSource,
    LogSource,
    MetricSource,
    PipelineSource,
    SourceQuery,
    SourceResult,
)
from contracts.collection.batch import QueryResult
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.enums import Severity, SourceStatus, SourceType
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import FrozenClock
from ingestion.capabilities.registry import (
    DEFAULT_CAPABILITY_SPECS,
    DefaultSourceRegistry,
    SourceRegistry,
    get_default_capability,
)

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 3, tzinfo=timezone.utc)


def _make_sample_seed(incident_id: str = "inc_001") -> IncidentSeed:
    return IncidentSeed(
        incident_id=incident_id,
        external_alert_id="alert-001",
        service="payment-api",
        environment="simulation",
        severity=Severity.CRITICAL,
        detected_at=FIXED_NOW,
        received_at=FIXED_NOW,
        summary="HTTP 500 rate exceeded threshold",
    )


class DummySource:
    """Minimal implementation of BaseSource for testing."""

    def __init__(
        self,
        source_type: SourceType,
        available: bool = True,
        fail_on_capability: bool = False,
    ) -> None:
        self.source_type = source_type
        self.available = available
        self.fail_on_capability = fail_on_capability

    def get_capability(
        self, incident: IncidentSeed | None = None
    ) -> SourceCapability:
        if self.fail_on_capability:
            raise ConnectionError(f"Cannot reach {self.source_type.value} source")
        return get_default_capability(self.source_type, available=self.available)

    def query(self, query: SourceQuery) -> SourceResult:
        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=f"dummy-{self.source_type.value}-adapter",
            source_status=SourceStatus.OK,
            records=[],
        )


# ── Registry & Catalog Generation ───────────────────────────────────


class TestSourceRegistryCapabilities:
    def test_empty_registry_includes_all_six_sources_marked_unavailable(self):
        """All six source categories must be present, never omitted (WORK_DIVISION §6.5)."""
        clock = FrozenClock(FIXED_NOW)
        registry = DefaultSourceRegistry(clock=clock)
        seed = _make_sample_seed("inc_100")

        catalog = registry.capabilities(seed)

        assert isinstance(catalog, SourceCapabilityCatalog)
        assert catalog.incident_id == "inc_100"
        assert catalog.generated_at == FIXED_NOW
        assert catalog.schema_version == "1.0"

        # Verify all 6 source types are included
        returned_types = {s.source_type for s in catalog.sources}
        expected_types = set(SourceType)
        assert returned_types == expected_types
        assert len(catalog.sources) == 6

        # All are marked unavailable since no adapters are registered
        for s in catalog.sources:
            assert s.available is False
            assert s.supported_query_fields == []
            assert s.maximum_window_seconds == 0
            assert s.maximum_items == 0

    def test_registered_sources_appear_as_available(self):
        clock = FrozenClock(FIXED_NOW)
        registry = DefaultSourceRegistry(clock=clock)
        registry.register_source(DummySource(SourceType.LOGS, available=True))
        registry.register_source(DummySource(SourceType.METRICS, available=True))

        catalog = registry.capabilities(_make_sample_seed("inc_200"))
        by_type = {s.source_type: s for s in catalog.sources}

        assert by_type[SourceType.LOGS].available is True
        assert "service" in by_type[SourceType.LOGS].supported_query_fields
        assert "pattern" in by_type[SourceType.LOGS].supported_query_fields
        assert by_type[SourceType.LOGS].maximum_window_seconds == 86400

        assert by_type[SourceType.METRICS].available is True
        assert "metric_name" in by_type[SourceType.METRICS].supported_query_fields

        # Unregistered sources remain unavailable
        assert by_type[SourceType.CHANGES].available is False
        assert by_type[SourceType.DEPLOYMENTS].available is False
        assert by_type[SourceType.PIPELINES].available is False
        assert by_type[SourceType.CONFIGURATION].available is False

    def test_all_six_sources_fully_registered(self):
        registry = DefaultSourceRegistry()
        for st in SourceType:
            registry.register_source(DummySource(st, available=True))

        catalog = registry.capabilities(_make_sample_seed())
        assert len(catalog.sources) == 6
        for s in catalog.sources:
            assert s.available is True
            assert len(s.supported_query_fields) > 0
            assert s.maximum_window_seconds > 0
            assert s.maximum_items > 0

    def test_failing_source_does_not_crash_catalog_generation(self):
        """A source failure must not crash capability generation (§6.8, §6.10)."""
        registry = DefaultSourceRegistry()
        registry.register_source(DummySource(SourceType.LOGS, available=True))
        # Metrics adapter throws on get_capability
        registry.register_source(
            DummySource(SourceType.METRICS, fail_on_capability=True)
        )

        catalog = registry.capabilities(_make_sample_seed())
        by_type = {s.source_type: s for s in catalog.sources}

        assert by_type[SourceType.LOGS].available is True
        assert by_type[SourceType.METRICS].available is False
        assert by_type[SourceType.METRICS].supported_query_fields == []

    def test_explicit_capability_override(self):
        registry = DefaultSourceRegistry()
        custom_cap = SourceCapability(
            source_type=SourceType.CONFIGURATION,
            available=True,
            supported_query_fields=["service", "keys", "custom_field"],
            maximum_window_seconds=12345,
            maximum_items=42,
        )
        registry.register_capability(custom_cap)

        catalog = registry.capabilities(_make_sample_seed())
        by_type = {s.source_type: s for s in catalog.sources}

        assert by_type[SourceType.CONFIGURATION] == custom_cap

    def test_unregister_source_and_capability(self):
        registry = DefaultSourceRegistry()
        registry.register_source(DummySource(SourceType.CHANGES, available=True))
        assert registry.get_source(SourceType.CHANGES) is not None

        registry.unregister_source(SourceType.CHANGES)
        assert registry.get_source(SourceType.CHANGES) is None

        catalog = registry.capabilities(_make_sample_seed())
        by_type = {s.source_type: s for s in catalog.sources}
        assert by_type[SourceType.CHANGES].available is False


# ── Output Contract & Schema Compliance ─────────────────────────────


class TestSourceCapabilitySchemaCompliance:
    def test_output_roundtrips_through_json(self):
        registry = DefaultSourceRegistry()
        for st in SourceType:
            registry.register_source(DummySource(st, available=True))

        catalog = registry.capabilities(_make_sample_seed("inc_schema_test"))
        json_str = catalog.model_dump_json()

        restored = SourceCapabilityCatalog.model_validate_json(json_str)
        assert restored == catalog

    def test_no_vendor_specific_types_in_json_output(self):
        registry = DefaultSourceRegistry()
        for st in SourceType:
            registry.register_source(DummySource(st, available=True))

        catalog = registry.capabilities(_make_sample_seed())
        raw_dict = json.loads(catalog.model_dump_json())

        assert isinstance(raw_dict["schema_version"], str)
        assert isinstance(raw_dict["incident_id"], str)
        assert isinstance(raw_dict["generated_at"], str)
        assert isinstance(raw_dict["sources"], list)

        for source in raw_dict["sources"]:
            assert isinstance(source["source_type"], str)
            assert isinstance(source["available"], bool)
            assert isinstance(source["supported_query_fields"], list)
            assert isinstance(source["maximum_window_seconds"], int)
            assert isinstance(source["maximum_items"], int)


# ── Source Protocols Conformance ────────────────────────────────────


class TestSourceProtocols:
    def test_runtime_checkable_protocols(self):
        class ConcreteLogSource:
            source_type = SourceType.LOGS

            def get_capability(self, incident=None):
                return get_default_capability(SourceType.LOGS)

            def query(self, query):
                return QueryResult(
                    query_id="q1",
                    source_type=SourceType.LOGS,
                    source_adapter="test",
                    source_status=SourceStatus.OK,
                )

        src = ConcreteLogSource()
        assert isinstance(src, BaseSource)
        assert isinstance(src, LogSource)

    def test_default_capability_specs_exist_for_all_six_sources(self):
        for st in SourceType:
            spec = DEFAULT_CAPABILITY_SPECS.get(st)
            assert spec is not None
            assert len(spec["supported_query_fields"]) > 0
            assert spec["maximum_window_seconds"] > 0
            assert spec["maximum_items"] > 0
