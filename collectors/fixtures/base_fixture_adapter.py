"""Base class for all fixture adapters providing deterministic test data."""

from __future__ import annotations

from typing import Any

from collectors.fixtures.data_loader import load_scenario_records
from collectors.interfaces import SourceQuery, SourceResult
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceStatus, SourceType
from collectors.specs import get_default_capability


class BaseFixtureAdapter:
    """Base implementation for fixture-based source adapters.

    Features (WORK_DIVISION §6.2, §6.6, §6.8):
    - Deterministic responses from recorded scenario fixtures.
    - Configurable availability, timeouts, and error states for resilience testing.
    - Parameter limit enforcement and truncation marking.
    - Typed SourceResult output conforming to shared contracts.
    - Credential-free data guarantee.
    """

    source_type: SourceType
    adapter_name: str = "fixture-adapter"

    def __init__(
        self,
        scenario_name: str | None = None,
        records: list[RawRecord] | None = None,
        available: bool = True,
        simulated_status: SourceStatus = SourceStatus.OK,
        simulated_warnings: list[str] | None = None,
        simulated_error: str | None = None,
        fail_on_capability: bool = False,
    ) -> None:
        self.available = available
        self.simulated_status = simulated_status
        self.simulated_warnings = list(simulated_warnings or [])
        self.simulated_error = simulated_error
        self.fail_on_capability = fail_on_capability

        if records is not None:
            self._records = list(records)
        elif scenario_name is not None:
            self._records = load_scenario_records(scenario_name, self.source_type)
        else:
            # Default to bad_db_config baseline
            self._records = load_scenario_records("bad_db_config", self.source_type)

    def get_capability(
        self, incident: IncidentSeed | None = None
    ) -> SourceCapability:
        """Return the capability descriptor for this fixture adapter."""
        if self.fail_on_capability:
            raise ConnectionError(
                f"Cannot communicate with {self.source_type.value} adapter."
            )
        return get_default_capability(self.source_type, available=self.available)

    def query(self, query: SourceQuery) -> SourceResult:
        """Execute query against loaded fixture data and return SourceResult."""
        if not self.available or self.simulated_status == SourceStatus.UNAVAILABLE:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings or ["Source is unavailable."],
            )

        if self.simulated_status == SourceStatus.TIMEOUT:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.TIMEOUT,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings or ["Source query timed out."],
            )

        if self.simulated_status == SourceStatus.ERROR:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings
                or [self.simulated_error or "Source query error occurred."],
            )

        # Apply limit parameter if provided
        limit: int | None = query.parameters.get("limit") if query.parameters else None
        records_to_return = list(self._records)
        truncated = False
        warnings = list(self.simulated_warnings)

        if limit is not None and limit > 0 and len(records_to_return) > limit:
            records_to_return = records_to_return[:limit]
            truncated = True
            warnings.append(f"Results truncated to limit of {limit} records.")

        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=self.simulated_status,
            truncated=truncated,
            records=records_to_return,
            warnings=warnings,
        )
