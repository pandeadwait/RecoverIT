"""Collectors package — source interfaces, registry, and collection service."""

from collectors.changes.fixture_adapter import FixtureChangeAdapter
from collectors.configuration.fixture_adapter import (
    FixtureConfigurationAdapter,
)
from collectors.deployments.fixture_adapter import FixtureDeploymentAdapter
from collectors.fixtures import (
    ReplayAdapter,
    create_scenario_adapters,
    list_available_scenarios,
    load_scenario_json,
    load_scenario_records,
)
from collectors.gateway import (
    CollectionService,
    DefaultCollectionService,
    validate_query,
)
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
from collectors.changes.git_adapter import LocalGitChangeAdapter
from collectors.logs.file_adapter import FileLogAdapter
from collectors.logs.fixture_adapter import FixtureLogAdapter
from collectors.metrics.fixture_adapter import FixtureMetricAdapter
from collectors.pipelines.fixture_adapter import FixturePipelineAdapter
from collectors.specs import DEFAULT_CAPABILITY_SPECS, get_default_capability

__all__ = [
    "BaseSource",
    "LogSource",
    "MetricSource",
    "ChangeSource",
    "DeploymentSource",
    "PipelineSource",
    "ConfigurationSource",
    "SourceQuery",
    "SourceResult",
    "FixtureLogAdapter",
    "FixtureMetricAdapter",
    "FixtureChangeAdapter",
    "FixtureDeploymentAdapter",
    "FixturePipelineAdapter",
    "FixtureConfigurationAdapter",
    "LocalGitChangeAdapter",
    "FileLogAdapter",
    "ReplayAdapter",
    "create_scenario_adapters",
    "list_available_scenarios",
    "load_scenario_json",
    "load_scenario_records",
    "CollectionService",
    "DefaultCollectionService",
    "validate_query",
    "DEFAULT_CAPABILITY_SPECS",
    "get_default_capability",
]
