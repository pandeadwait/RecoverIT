"""Fixture adapter for application and container logs."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import LogSource
from contracts.enums import SourceType


class FixtureLogAdapter(BaseFixtureAdapter, LogSource):
    """Fixture adapter returning log records."""

    source_type: SourceType = SourceType.LOGS
    adapter_name: str = "fixture-log-adapter"
