"""Fixture adapter for operational metrics."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import MetricSource
from contracts.enums import SourceType


class FixtureMetricAdapter(BaseFixtureAdapter, MetricSource):
    """Fixture adapter returning time-series metric records."""

    source_type: SourceType = SourceType.METRICS
    adapter_name: str = "fixture-metric-adapter"
