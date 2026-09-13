"""Loader for recorded scenario fixtures and raw records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.collection.batch import RawRecord
from contracts.enums import SourceType

FIXTURES_DATA_DIR = Path(__file__).resolve().parent / "data"

SCENARIO_NAMES = [
    "bad_db_config",
    "memory_exhaustion",
    "dependency_incompatibility",
    "real_db_outage",
    "coincidental_deployment",
]


def load_scenario_json(scenario_name: str) -> dict[str, Any]:
    """Load the raw JSON dict for a given scenario name."""
    path = FIXTURES_DATA_DIR / f"{scenario_name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Scenario fixture '{scenario_name}' not found in {FIXTURES_DATA_DIR}."
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


def list_available_scenarios() -> list[str]:
    """Return list of available scenario identifiers."""
    return list(SCENARIO_NAMES)
