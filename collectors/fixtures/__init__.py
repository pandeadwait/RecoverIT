"""Fixtures subpackage — scenario data loader, base fixture adapter, and replay adapter."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.fixtures.data_loader import (
    CANONICAL_SCENARIOS,
    SCENARIO_NAMES,
    canonical_scenario_id,
    list_available_scenarios,
    load_scenario_json,
    load_scenario_records,
    resolve_scenario_name,
)
from collectors.fixtures.replay_adapter import ReplayAdapter
from collectors.interfaces import BaseSource
from contracts.enums import SourceType


# Historical replay fixtures intentionally cover the six recorded source
# families.  HEALTH is a live adapter responsibility and must not be faked by
# inserting scenario-specific canned responses.
FIXTURE_SOURCE_TYPES: tuple[SourceType, ...] = (
    SourceType.LOGS,
    SourceType.METRICS,
    SourceType.CHANGES,
    SourceType.DEPLOYMENTS,
    SourceType.PIPELINES,
    SourceType.CONFIGURATION,
)


def create_scenario_adapters(
    scenario_name: str,
) -> dict[SourceType, BaseSource]:
    """Instantiate fixture adapters for all six source types loaded with the given scenario."""
    from collectors.changes.fixture_adapter import FixtureChangeAdapter
    from collectors.configuration.fixture_adapter import (
        FixtureConfigurationAdapter,
    )
    from collectors.deployments.fixture_adapter import FixtureDeploymentAdapter
    from collectors.logs.fixture_adapter import FixtureLogAdapter
    from collectors.metrics.fixture_adapter import FixtureMetricAdapter
    from collectors.pipelines.fixture_adapter import FixturePipelineAdapter

    return {
        SourceType.LOGS: FixtureLogAdapter(scenario_name=scenario_name),
        SourceType.METRICS: FixtureMetricAdapter(scenario_name=scenario_name),
        SourceType.CHANGES: FixtureChangeAdapter(scenario_name=scenario_name),
        SourceType.DEPLOYMENTS: FixtureDeploymentAdapter(
            scenario_name=scenario_name
        ),
        SourceType.PIPELINES: FixturePipelineAdapter(
            scenario_name=scenario_name
        ),
        SourceType.CONFIGURATION: FixtureConfigurationAdapter(
            scenario_name=scenario_name
        ),
    }


__all__ = [
    "BaseFixtureAdapter",
    "ReplayAdapter",
    "load_scenario_json",
    "load_scenario_records",
    "list_available_scenarios",
    "create_scenario_adapters",
    "SCENARIO_NAMES",
    "CANONICAL_SCENARIOS",
    "canonical_scenario_id",
    "resolve_scenario_name",
    "FIXTURE_SOURCE_TYPES",
]
