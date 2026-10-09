"""Pipelines collector subpackage."""

from collectors.interfaces import PipelineSource
from collectors.pipelines.fixture_adapter import FixturePipelineAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter

__all__ = ["PipelineSource", "GitHubActionsPipelineAdapter", "FixturePipelineAdapter"]
