"""Collectors package — source interfaces, registry, collection service, and adapters."""

from collectors.base import SourceAdapter
from collectors.changes.fixture_adapter import FixtureChangeAdapter
from collectors.changes.git_adapter import LocalGitChangeAdapter as LegacyGitChangeAdapter
from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.configuration.fixture_adapter import (
    FixtureConfigurationAdapter,
)
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.deployments.fixture_adapter import FixtureDeploymentAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.fixtures import (
    FIXTURE_SOURCE_TYPES,
    ReplayAdapter,
    create_scenario_adapters,
    list_available_scenarios,
    load_scenario_json,
    load_scenario_records,
)
from collectors.gateway import (
    CollectionService,
    DefaultCollectionService,
    validate_query as legacy_validate_query,
)
from collectors.health.http_health import HttpHealthAdapter
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
from collectors.logs.file import FileLogAdapter
from collectors.logs.file_adapter import FileLogAdapter as LegacyFileLogAdapter
from collectors.logs.fixture_adapter import FixtureLogAdapter
from collectors.metrics.fixture_adapter import FixtureMetricAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.fixture_adapter import FixturePipelineAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from collectors.registry import DuplicateSourceAdapterError, SourceRegistry
from collectors.service import Clock, DefaultCollectionService as LiveCollectionService, IdentifierFactory
from collectors.specs import DEFAULT_CAPABILITY_SPECS, get_default_capability
from collectors.validation import validate_query

__all__ = [
    # Canonical protocols and services (LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10)
    "SourceAdapter",
    "SourceRegistry",
    "DuplicateSourceAdapterError",
    "LiveCollectionService",
    "DefaultCollectionService",
    "Clock",
    "IdentifierFactory",
    "validate_query",
    # Real reference adapters (§10.4)
    "LocalGitChangeAdapter",
    "FileLogAdapter",
    "PrometheusMetricAdapter",
    "GitHubActionsPipelineAdapter",
    "KubernetesDeploymentAdapter",
    "GitConfigurationAdapter",
    "HttpHealthAdapter",
    # Legacy interfaces and adapters retained for backward compatibility
    "BaseSource",
    "LogSource",
    "MetricSource",
    "ChangeSource",
    "DeploymentSource",
    "PipelineSource",
    "ConfigurationSource",
    "SourceQuery",
    "SourceResult",
    "CollectionService",
    "LegacyCollectionService",
    "LegacyGitChangeAdapter",
    "LegacyFileLogAdapter",
    "FixtureLogAdapter",
    "FixtureMetricAdapter",
    "FixtureChangeAdapter",
    "FixtureDeploymentAdapter",
    "FixturePipelineAdapter",
    "FixtureConfigurationAdapter",
    "ReplayAdapter",
    "FIXTURE_SOURCE_TYPES",
    "create_scenario_adapters",
    "list_available_scenarios",
    "load_scenario_json",
    "load_scenario_records",
    "DEFAULT_CAPABILITY_SPECS",
    "get_default_capability",
]
