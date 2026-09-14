"""Loader for recorded scenario fixtures and raw records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.collection.batch import RawRecord
from contracts.enums import SourceType

FIXTURES_DATA_DIR = Path(__file__).resolve().parent / "data"

CANONICAL_SCENARIOS = [
    "incident_001",
    "incident_002",
    "incident_003",
    "incident_004",
    "incident_005",
]

LEGACY_SCENARIOS = [
    "bad_db_config",
    "memory_exhaustion",
    "dependency_incompatibility",
    "real_db_outage",
    "coincidental_deployment",
]

SCENARIO_ALIASES: dict[str, str] = {
    "incident_001": "bad_db_config",
    "incident_002": "memory_exhaustion",
    "incident_003": "dependency_incompatibility",
    "incident_004": "real_db_outage",
    "incident_005": "coincidental_deployment",
    "bad_db_config": "bad_db_config",
    "memory_exhaustion": "memory_exhaustion",
    "dependency_incompatibility": "dependency_incompatibility",
    "real_db_outage": "real_db_outage",
    "coincidental_deployment": "coincidental_deployment",
}

ALIAS_TO_CANONICAL: dict[str, str] = {
    "bad_db_config": "incident_001",
    "memory_exhaustion": "incident_002",
    "dependency_incompatibility": "incident_003",
    "real_db_outage": "incident_004",
    "coincidental_deployment": "incident_005",
    "incident_001": "incident_001",
    "incident_002": "incident_002",
    "incident_003": "incident_003",
    "incident_004": "incident_004",
    "incident_005": "incident_005",
}

SCENARIO_NAMES = CANONICAL_SCENARIOS + LEGACY_SCENARIOS


def resolve_scenario_name(scenario_name: str) -> str:
    """Resolve a canonical or alias scenario name to its underlying fixture data file basename."""
    return SCENARIO_ALIASES.get(scenario_name, scenario_name)


def canonical_scenario_id(scenario_name: str) -> str:
    """Return the neutral canonical scenario identifier (e.g. incident_001) for a scenario."""
    return ALIAS_TO_CANONICAL.get(scenario_name, scenario_name)


def load_scenario_json(scenario_name: str) -> dict[str, Any]:
    """Load the raw JSON dict for a given scenario name (supports canonical IDs and aliases)."""
    file_basename = resolve_scenario_name(scenario_name)
    path = FIXTURES_DATA_DIR / f"{file_basename}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Scenario fixture '{scenario_name}' (resolved as '{file_basename}') not found in {FIXTURES_DATA_DIR}."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_scenario_records(
    scenario_name: str, source_type: SourceType
) -> list[RawRecord]:
    """Load and parse records for a specific source type within a scenario."""
    data = load_scenario_json(scenario_name)
    sources = data.get("sources", {})
    raw_list = sources.get(source_type.value, [])

    records: list[RawRecord] = []
    for item in raw_list:
        records.append(RawRecord.model_validate(item))
    return records


def list_available_scenarios(only_canonical: bool = False) -> list[str]:
    """Return list of available scenario identifiers."""
    if only_canonical:
        return list(CANONICAL_SCENARIOS)
    return list(SCENARIO_NAMES)

