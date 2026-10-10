"""Live source adapters and collection services used by the LangGraph runtime."""

from collectors.base import SourceAdapter
from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.health.http_health import HttpHealthAdapter
from collectors.logs.file import FileLogAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from collectors.registry import DuplicateSourceAdapterError, SourceRegistry
from collectors.service import Clock, DefaultCollectionService, IdentifierFactory
from collectors.specs import DEFAULT_CAPABILITY_SPECS, get_default_capability
from collectors.validation import validate_query

__all__ = [
    # Canonical protocols and services
    "SourceAdapter",
    "SourceRegistry",
    "DuplicateSourceAdapterError",
    "DefaultCollectionService",
    "Clock",
    "IdentifierFactory",
    "validate_query",
    # Real source adapters
    "LocalGitChangeAdapter",
    "FileLogAdapter",
    "PrometheusMetricAdapter",
    "GitHubActionsPipelineAdapter",
    "KubernetesDeploymentAdapter",
    "GitConfigurationAdapter",
    "HttpHealthAdapter",
    "DEFAULT_CAPABILITY_SPECS",
    "get_default_capability",
]
