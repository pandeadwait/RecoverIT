"""Unit tests for SourceRegistry.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.2:
- Registering two adapters for the same source type raises DuplicateSourceAdapterError.
- capabilities() always returns one entry for every SourceType across all 7 canonical source types.
- Unregistered source types are returned as unavailable.
- Capability discovery failure is converted to an unavailable capability with a sanitized reason.
- Registry behavior does not depend on service name or scenario.
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from collectors.registry import DuplicateSourceAdapterError, SourceRegistry
from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.common import Severity
from contracts.enums import SourceType
from contracts.incident.schemas import IncidentSeed
from tests.support.scripted_source import ScriptedSourceAdapter


@pytest.fixture
def sample_incident() -> IncidentSeed:
    now = datetime.now(timezone.utc)
    return IncidentSeed(
        incident_id="inc-registry-001",
        external_alert_id="alt-reg-001",
        service="order-service",
        environment="production",
        severity=Severity.WARNING,
        detected_at=now,
        received_at=now,
        summary="Registry Test Incident",
    )



def test_empty_registry_reports_all_seven_source_types_unavailable(sample_incident: IncidentSeed):
    """An empty registry must still return exactly all 7 canonical source types as unavailable."""
    registry = SourceRegistry()
    catalog = registry.capabilities(sample_incident)

    assert isinstance(catalog, SourceCapabilityCatalog)
    assert catalog.incident_id == sample_incident.incident_id
    assert len(catalog.sources) == len(SourceType)

    source_types_in_catalog = {cap.source_type for cap in catalog.sources}
    assert source_types_in_catalog == set(SourceType)

    for cap in catalog.sources:
        assert cap.available is False
        assert cap.adapter_name == "none"
        assert cap.unavailable_reason is not None
        assert "not configured" in cap.unavailable_reason


def test_duplicate_adapter_registration_raises():
    """Registering two adapters for the same source type raises DuplicateSourceAdapterError."""
    registry = SourceRegistry()
    adapter1 = ScriptedSourceAdapter(
        source_type=SourceType.LOGS,
        adapter_name="logs_primary",
    )
    adapter2 = ScriptedSourceAdapter(
        source_type=SourceType.LOGS,
        adapter_name="logs_secondary",
    )

    registry.register(adapter1)
    assert registry.get(SourceType.LOGS) is adapter1

    with pytest.raises(DuplicateSourceAdapterError) as exc_info:
        registry.register(adapter2)

    assert exc_info.value.source_type == SourceType.LOGS
    assert exc_info.value.existing_adapter == "logs_primary"
    assert exc_info.value.new_adapter == "logs_secondary"
    assert "Duplicate adapter for source type 'logs'" in str(exc_info.value)


def test_registry_initialization_with_iterable_and_duplicates():
    """SourceRegistry can be initialized with an iterable of adapters."""
    adapter1 = ScriptedSourceAdapter(source_type=SourceType.LOGS, adapter_name="log_adp")
    adapter2 = ScriptedSourceAdapter(source_type=SourceType.CHANGES, adapter_name="git_adp")

    registry = SourceRegistry([adapter1, adapter2])
    assert registry.get(SourceType.LOGS) is adapter1
    assert registry.get(SourceType.CHANGES) is adapter2
    assert registry.get(SourceType.METRICS) is None

    # Duplicates in constructor raise
    with pytest.raises(DuplicateSourceAdapterError):
        SourceRegistry([adapter1, adapter1])


def test_registered_adapter_reports_capability(sample_incident: IncidentSeed):
    """Registered adapter's capability is reflected in the catalog."""
    registry = SourceRegistry()
    git_adapter = ScriptedSourceAdapter(
        source_type=SourceType.CHANGES,
        adapter_name="git_adapter",
        available=True,
    )
    registry.register(git_adapter)

    catalog = registry.capabilities(sample_incident)
    assert len(catalog.sources) == len(SourceType)

    cap_map = {cap.source_type: cap for cap in catalog.sources}
    assert cap_map[SourceType.CHANGES].available is True
    assert cap_map[SourceType.CHANGES].adapter_name == "git_adapter"

    # Other sources are unavailable
    for st in SourceType:
        if st != SourceType.CHANGES:
            assert cap_map[st].available is False


def test_discovery_exception_is_sanitized_and_marked_unavailable(sample_incident: IncidentSeed):
    """Adapter whose get_capability() raises is marked available=False without raising."""
    class CrashingAdapter(ScriptedSourceAdapter):
        def get_capability(self, incident: IncidentSeed | None = None):
            raise RuntimeError("Underlying system crashed with sensitive token secret123!")

    registry = SourceRegistry()
    crashing = CrashingAdapter(
        source_type=SourceType.METRICS,
        adapter_name="crashing_prom",
    )
    registry.register(crashing)

    # Must not raise
    catalog = registry.capabilities(sample_incident)
    cap_map = {cap.source_type: cap for cap in catalog.sources}

    metrics_cap = cap_map[SourceType.METRICS]
    assert metrics_cap.available is False
    assert metrics_cap.adapter_name == "crashing_prom"
    assert metrics_cap.unavailable_reason == "Source capability discovery failed."
    # Ensure raw exception message (and token) was not leaked into unavailable_reason
    assert "secret123" not in metrics_cap.unavailable_reason
