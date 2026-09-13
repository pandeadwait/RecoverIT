"""Fixture adapter for source code and Git changes."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import ChangeSource
from contracts.enums import SourceType


class FixtureChangeAdapter(BaseFixtureAdapter, ChangeSource):
    """Fixture adapter returning Git commit and change records."""

    source_type: SourceType = SourceType.CHANGES
    adapter_name: str = "fixture-change-adapter"
