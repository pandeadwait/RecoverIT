"""Replay adapter for offline deterministic test execution."""

from __future__ import annotations

from typing import Any

from collectors.interfaces import BaseSource, SourceQuery, SourceResult
from contracts.collection.batch import QueryResult
from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceStatus, SourceType
from collectors.specs import get_default_capability


class ReplayAdapter(BaseSource):
    """Replays pre-recorded query results deterministically for offline testing.

    Responsibilities (WORK_DIVISION §6.2, §6.8, §6.9):
    - Map incoming query IDs or signatures to recorded QueryResult instances.
    - Guarantee identical replay output across test runs.
    - Avoid any network or live system access.
    """

    adapter_name: str = "replay-adapter"

    def __init__(
        self,
        source_type: SourceType,
        recorded_results: dict[str, QueryResult | dict[str, Any]] | None = None,
        default_capability: SourceCapability | None = None,
    ) -> None:
        self.source_type = source_type
        self._default_capability = default_capability
        self._recorded: dict[str, QueryResult] = {}
        if recorded_results:
            self.load_recorded(recorded_results)

    def record(self, query_id: str, result: QueryResult) -> None:
        """Store a QueryResult under a query_id for subsequent replay."""
        self._recorded[query_id] = result

    def load_recorded(
        self, recorded_results: dict[str, QueryResult | dict[str, Any]]
    ) -> None:
        """Load multiple recorded query results from a dict."""
        for qid, res in recorded_results.items():
            if isinstance(res, QueryResult):
                self._recorded[qid] = res
            else:
                self._recorded[qid] = QueryResult.model_validate(res)

    def get_capability(
        self, incident: IncidentSeed | None = None
    ) -> SourceCapability:
        """Return the capability descriptor for this replay source."""
        if self._default_capability is not None:
            return self._default_capability
        return get_default_capability(self.source_type, available=True)

    def query(self, query: SourceQuery) -> SourceResult:
        """Return the recorded QueryResult for the query_id."""
        if query.query_id in self._recorded:
            return self._recorded[query.query_id]

        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=SourceStatus.UNAVAILABLE,
            truncated=False,
            records=[],
            warnings=[
                f"ReplayAdapter has no recorded result for query_id '{query.query_id}'."
            ],
        )
