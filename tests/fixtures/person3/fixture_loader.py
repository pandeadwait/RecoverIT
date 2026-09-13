"""
Fixture loader utilities for Person 3 scenario test fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.collection.schemas import SourceCapabilityCatalog
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.incident.schemas import IncidentSeed

FIXTURES_DIR = Path(__file__).parent


def load_json_fixture(filename: str) -> dict[str, Any]:
    """Load a raw JSON dictionary from tests/fixtures/person3/."""
    file_path = FIXTURES_DIR / filename
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_incident_seed(filename: str) -> IncidentSeed:
    """Load an IncidentSeed contract instance from a JSON fixture."""
    data = load_json_fixture(filename)
    return IncidentSeed.model_validate(data)


def load_catalog(filename: str = "catalog_default.json") -> SourceCapabilityCatalog:
    """Load a SourceCapabilityCatalog contract instance from a JSON fixture."""
    data = load_json_fixture(filename)
    return SourceCapabilityCatalog.model_validate(data)


def load_context_snapshot(filename: str) -> IncidentContextSnapshot:
    """Load an IncidentContextSnapshot contract instance from a JSON fixture."""
    data = load_json_fixture(filename)
    return IncidentContextSnapshot.model_validate(data)


def get_fixture_path(filename: str) -> Path:
    """Return the absolute path to a fixture file."""
    return FIXTURES_DIR / filename
