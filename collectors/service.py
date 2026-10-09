"""Collection service coordinating concurrent, bounded evidence collection.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.3, this service executes
an EvidenceQueryPlan against registered read-only source adapters and produces
a canonical RawEvidenceBatch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Protocol, runtime_checkable
import uuid

from collectors.base import SourceAdapter
from collectors.registry import SourceRegistry
from collectors.validation import validate_query
from contracts.collection.schemas import (
    EvidenceQuery,
    EvidenceQueryPlan,
    RawEvidenceBatch,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.enums import SourceStatus, SourceType
from contracts.errors.schemas import StructuredError

logger = logging.getLogger(__name__)


@runtime_checkable
class Clock(Protocol):
    """Protocol for time provider."""

    def now(self) -> datetime:
        ...


@runtime_checkable
class IdentifierFactory(Protocol):
    """Protocol for generating unique identifiers."""

    def new_id(self, prefix: str) -> str:
        ...


class SystemClock:
    """Default UTC clock implementation."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UUIDIdentifierFactory:
    """Default identifier factory using UUID4."""

    def new_id(self, prefix: str = "id") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DefaultCollectionService:
    """Executes evidence query plans across registered source adapters.

    Guarantees:
    - Bounded concurrency with max_concurrency.
    - Query-level timeout enforcement.
    - Strict validation against capability descriptors.
    - Structured errors on validation failure, timeout, or source error.
    - Exactly one SourceResult per EvidenceQuery in the plan.
    - Preserves timestamps and provenance without synthesizing fake evidence.
    """

    def __init__(
        self,
        registry: SourceRegistry,
        clock: Clock | None = None,
        id_factory: IdentifierFactory | None = None,
        max_concurrency: int = 4,
        query_timeout_seconds: float = 10.0,
    ) -> None:
        self.registry = registry
        self.clock = clock if clock is not None else SystemClock()
        self.id_factory = id_factory if id_factory is not None else UUIDIdentifierFactory()
        self.max_concurrency = max_concurrency
        self.query_timeout_seconds = query_timeout_seconds

    async def collect(
        self,
        plan: EvidenceQueryPlan,
        capabilities: SourceCapabilityCatalog,
    ) -> RawEvidenceBatch:
        """Execute all queries in the plan and aggregate into a RawEvidenceBatch."""
        cap_by_type: dict[SourceType, SourceCapability] = {
            cap.source_type: cap for cap in capabilities.sources
        }

        semaphore = asyncio.Semaphore(self.max_concurrency)
        batch_errors: list[StructuredError] = []

        async def _execute_single(
            query: EvidenceQuery,
        ) -> tuple[SourceResult, list[StructuredError]]:
            async with semaphore:
                return await self._execute_query(query, cap_by_type.get(query.source_type))

        tasks = [_execute_single(q) for q in plan.queries]
        results_and_errors = await asyncio.gather(*tasks)

        collected_results: list[SourceResult] = []
        for res, errs in results_and_errors:
            collected_results.append(res)
            batch_errors.extend(errs)

        return RawEvidenceBatch(
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            batch_id=self.id_factory.new_id("batch"),
            collected_at=self.clock.now(),
            results=collected_results,
            errors=batch_errors,
        )

    async def _execute_query(
        self,
        query: EvidenceQuery,
        capability: SourceCapability | None,
    ) -> tuple[SourceResult, list[StructuredError]]:
        start_time = self.clock.now()
        adapter_name = capability.adapter_name if capability else "unknown"

        # 1. Validation against capability
        validation_errors = validate_query(query, capability)
        if validation_errors:
            # Check if source is unavailable vs parameter validation failure
            is_unavail = any(e.code == "SOURCE_UNAVAILABLE" for e in validation_errors)
            status = SourceStatus.UNAVAILABLE if is_unavail else SourceStatus.ERROR
            end_time = self.clock.now()
            result = SourceResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter=adapter_name,
                source_status=status,
                truncated=False,
                records=[],
                warnings=[e.message for e in validation_errors],
                started_at=start_time,
                completed_at=end_time,
            )
            return result, validation_errors

        # 2. Lookup adapter in registry
        adapter = self.registry.get(query.source_type)
        if adapter is None:
            end_time = self.clock.now()
            err = StructuredError(
                code="SOURCE_UNREGISTERED",
                message=f"No adapter registered for source type '{query.source_type.value}'.",
                retryable=False,
                stage="collect_evidence",
                source_type=query.source_type,
                details={"query_id": query.query_id},
            )
            result = SourceResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter="none",
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=[err.message],
                started_at=start_time,
                completed_at=end_time,
            )
            return result, [err]

        # 3. Execute with timeout
        try:
            query_task = adapter.query(query)
            if asyncio.iscoroutine(query_task):
                source_result = await asyncio.wait_for(
                    query_task,
                    timeout=self.query_timeout_seconds,
                )
            else:
                # In case adapter returns directly or is synchronous helper
                source_result = query_task

            # If the source returned zero records and status was OK, mark EMPTY
            if source_result.source_status == SourceStatus.OK and len(source_result.records) == 0:
                source_result = source_result.model_copy(
                    update={"source_status": SourceStatus.EMPTY}
                )

            return source_result, []

        except asyncio.TimeoutError:
            end_time = self.clock.now()
            logger.warning(
                "Query '%s' to adapter '%s' timed out after %.1fs",
                query.query_id,
                adapter.adapter_name,
                self.query_timeout_seconds,
            )
            err = StructuredError(
                code="COLLECTION_TIMEOUT",
                message=(
                    f"Query '{query.query_id}' timed out after "
                    f"{self.query_timeout_seconds:.1f}s on adapter '{adapter.adapter_name}'."
                ),
                retryable=True,
                stage="collect_evidence",
                source_type=query.source_type,
                details={
                    "query_id": query.query_id,
                    "timeout_seconds": self.query_timeout_seconds,
                    "adapter_name": adapter.adapter_name,
                },
            )
            result = SourceResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter=adapter.adapter_name,
                source_status=SourceStatus.TIMEOUT,
                truncated=False,
                records=[],
                warnings=[err.message],
                started_at=start_time,
                completed_at=end_time,
            )
            return result, [err]

        except Exception as exc:
            end_time = self.clock.now()
            logger.exception(
                "Unexpected failure executing query '%s' on adapter '%s': %s",
                query.query_id,
                adapter.adapter_name,
                exc,
            )
            err = StructuredError(
                code="SOURCE_QUERY_FAILED",
                message=f"Source adapter '{adapter.adapter_name}' failed: {type(exc).__name__}: {str(exc)}",
                retryable=False,
                stage="collect_evidence",
                source_type=query.source_type,
                details={
                    "query_id": query.query_id,
                    "adapter_name": adapter.adapter_name,
                    "error_type": type(exc).__name__,
                },
            )
            result = SourceResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter=adapter.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[err.message],
                started_at=start_time,
                completed_at=end_time,
            )
            return result, [err]
