"""Fixture adapter for configuration changes."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import ConfigurationSource
from contracts.enums import SourceType


class FixtureConfigurationAdapter(BaseFixtureAdapter, ConfigurationSource):
    """Fixture adapter returning configuration change records."""

    source_type: SourceType = SourceType.CONFIGURATION
    adapter_name: str = "fixture-configuration-adapter"
