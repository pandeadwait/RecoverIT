"""Scripted test source adapter for testing collection, graph, and reasoning workflows.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §17.2 and §18, this adapter provides
a deterministic, programmable SourceAdapter implementation for tests without requiring
external systems or network access.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from collectors.base import SourceAdapter
from contracts.collection.schemas import (
    EvidenceQuery,
    RawRecord,
    SourceCapability,
    SourceResult,
)
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed


class ScriptedSourceAdapter(SourceAdapter):
    """Programmable in-memory SourceAdapter for unit and integration testing."""

    def __init__(
        self,
        source_type: SourceType = SourceType.HEALTH,
        adapter_name: str = "scripted_source",
        available: bool = True,
        supported_fields: list[str] | None = None,
        canned_records: list[RawRecord] | None = None,
        canned_status: SourceStatus = SourceStatus.OK,
        delay_seconds: float = 0.0,
        fail_with_exception: Exception | None = None,
        query_handler: Callable[[EvidenceQuery], SourceResult] | None = None,
        unavailable_reason: str | None = None,
    ) -> None:
        self.source_type = source_type
        self.adapter_name = adapter_name
        self.available = available
        self.supported_fields = supported_fields if supported_fields is not None else [
            "service",
            "limit",
            "start_time",
            "end_time",
            "query",
            "filter",
        ]
        self.canned_records = list(canned_records or [])
        self.canned_status = canned_status
        self.delay_seconds = delay_seconds
        self.fail_with_exception = fail_with_exception
        self.query_handler = query_handler
        self.unavailable_reason = unavailable_reason

        self.recorded_queries: list[EvidenceQuery] = []

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the programmed capability descriptor."""
        return SourceCapability(
            source_type=self.source_type,
            available=self.available,
            supported_query_fields=list(self.supported_fields),
            maximum_window_seconds=86400 * 7,
            maximum_items=1000,
            adapter_name=self.adapter_name,
            unavailable_reason=self.unavailable_reason if not self.available else None,
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Record the query and return programmed or canned results."""
        self.recorded_queries.append(query)
        started_at = datetime.now(timezone.utc)

        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.fail_with_exception is not None:
            raise self.fail_with_exception

        if self.query_handler is not None:
            return self.query_handler(query)

        completed_at = datetime.now(timezone.utc)

        if not self.available:
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=["Source is currently configured as unavailable."],
                started_at=started_at,
                completed_at=completed_at,
            )

        # Apply time filtering if present in parameters
        params = query.parameters or {}
        records_to_return = list(self.canned_records)

        start_param = params.get("start_time") or params.get("since")
        end_param = params.get("end_time") or params.get("until")
        if start_param:
            try:
                start_dt = datetime.fromisoformat(str(start_param).replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                records_to_return = [r for r in records_to_return if r.event_time is None or r.event_time >= start_dt]
            except Exception:
                pass
        if end_param:
            try:
                end_dt = datetime.fromisoformat(str(end_param).replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                records_to_return = [r for r in records_to_return if r.event_time is None or r.event_time <= end_dt]
            except Exception:
                pass

        # Apply service filtering if present
        svc_param = params.get("service")
        if svc_param:
            records_to_return = [
                r for r in records_to_return
                if not isinstance(r.payload, dict) or r.payload.get("service") in (None, svc_param)
            ]

        # Apply pattern filtering if present
        pat_param = params.get("pattern")
        if pat_param:
            records_to_return = [
                r for r in records_to_return
                if pat_param in str(r.payload)
            ]

        # Apply limit if present in parameters
        limit = params.get("limit")
        truncated = False
        if limit is not None:
            try:
                limit_int = int(limit)
                if len(records_to_return) > limit_int:
                    records_to_return = records_to_return[:limit_int]
                    truncated = True
            except (ValueError, TypeError):
                pass

        status = self.canned_status
        if status == SourceStatus.OK and len(records_to_return) == 0:
            status = SourceStatus.EMPTY

        return SourceResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=status,
            truncated=truncated,
            records=records_to_return,
            warnings=["Truncated by limit"] if truncated else [],
            started_at=started_at,
            completed_at=completed_at,
        )
