"""Deployments collector subpackage."""

from collectors.deployments.fixture_adapter import FixtureDeploymentAdapter
from collectors.interfaces import DeploymentSource

__all__ = ["DeploymentSource", "FixtureDeploymentAdapter"]
