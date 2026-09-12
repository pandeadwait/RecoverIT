"""Log collector subpackage."""

from collectors.interfaces import LogSource
from collectors.logs.fixture_adapter import FixtureLogAdapter

__all__ = ["LogSource", "FixtureLogAdapter"]
