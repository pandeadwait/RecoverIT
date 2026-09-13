"""Fixture adapter for CI/CD pipeline runs."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import PipelineSource
from contracts.enums import SourceType


class FixturePipelineAdapter(BaseFixtureAdapter, PipelineSource):
    """Fixture adapter returning CI/CD pipeline run records."""

    source_type: SourceType = SourceType.PIPELINES
    adapter_name: str = "fixture-pipeline-adapter"
