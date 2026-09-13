"""Fixture adapter for deployment history."""

from __future__ import annotations

from collectors.fixtures.base_fixture_adapter import BaseFixtureAdapter
from collectors.interfaces import DeploymentSource
from contracts.enums import SourceType


class FixtureDeploymentAdapter(BaseFixtureAdapter, DeploymentSource):
    """Fixture adapter returning deployment history records."""

    source_type: SourceType = SourceType.DEPLOYMENTS
    adapter_name: str = "fixture-deployment-adapter"
