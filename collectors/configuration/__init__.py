"""Configuration collector subpackage."""

from collectors.configuration.fixture_adapter import (
    FixtureConfigurationAdapter,
)
from collectors.interfaces import ConfigurationSource

__all__ = ["ConfigurationSource", "FixtureConfigurationAdapter"]
