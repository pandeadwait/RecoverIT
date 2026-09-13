"""Pipelines collector subpackage."""

from collectors.interfaces import PipelineSource
from collectors.pipelines.fixture_adapter import FixturePipelineAdapter

__all__ = ["PipelineSource", "FixturePipelineAdapter"]
