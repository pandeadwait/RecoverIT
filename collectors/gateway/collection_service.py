"""Collection service coordinating multi-source evidence query execution.

Per WORK_DIVISION §6.2, §6.6, §6.7, and §6.8, CollectionService executes an
EvidenceQueryPlan against registered source adapters and produces a RawEvidenceBatch.
"""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import datetime
from typing import Any, Protocol

from collectors.interfaces import BaseSource, SourceRegistry, SourceResult
from contracts.collection.batch import QueryResult, RawEvidenceBatch
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.enums import Severity, SourceStatus, SourceType
from contracts.errors import StructuredError
from contracts.incident.seed import IncidentSeed

logger = logging.getLogger(__name__)


def _parse_time_value(val: Any) -> datetime | None:
    """Parse a datetime instance or ISO string into a timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            # Replace Z with +00:00 for standard fromisoformat parsing
            clean = val.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            return None
    return None


def validate_query(
    query: EvidenceQuery, capability: SourceCapability | None
) -> list[StructuredError]:
    """Validate a single query against the source capability descriptor.

    Enforces collection rules from WORK_DIVISION §6.7:
    - Source must be available in catalog.
    - Parameters must use supported query fields only.
    - Time-window must not exceed maximum_window_seconds.
    """
    errors: list[StructuredError] = []

    # 1. Source availability check
    if capability is None or not capability.available:
        errors.append(
            StructuredError(
                code="SOURCE_UNAVAILABLE",
                message=(
                    f"Source type '{query.source_type.value}' is unavailable "
                    "or not registered in capability catalog."
                ),
                retryable=True,
                source=query.source_type.value,
                details={"query_id": query.query_id},
            )
        )
        return errors

    # 2. Supported query fields validation
    if query.parameters:
        unsupported = [
            field
            for field in query.parameters.keys()
            if field not in capability.supported_query_fields
        ]
        if unsupported:
            errors.append(
                StructuredError(
                    code="INVALID_QUERY_FIELDS",
                    message=(
                        f"Query '{query.query_id}' contains unsupported fields for source "
                        f"'{query.source_type.value}': {sorted(unsupported)}. "
                        f"Supported fields: {sorted(capability.supported_query_fields)}."
                    ),
                    retryable=False,
                    source=query.source_type.value,
                    details={
                        "query_id": query.query_id,
                        "unsupported_fields": sorted(unsupported),
                        "supported_fields": sorted(capability.supported_query_fields),
                    },
                )
            )

    # 3. Time window bounds validation
    start = _parse_time_value(
        query.parameters.get("start_time") or query.parameters.get("since")
    )
    end = _parse_time_value(
        query.parameters.get("end_time") or query.parameters.get("until")
    )
    if start is not None and end is not None:
        delta_seconds = abs((end - start).total_seconds())
        if delta_seconds > capability.maximum_window_seconds:
            errors.append(
                StructuredError(
                    code="QUERY_WINDOW_EXCEEDED",
                    message=(
                        f"Query '{query.query_id}' time window ({delta_seconds:.0f}s) "
                        f"exceeds maximum allowed window ({capability.maximum_window_seconds}s) "
                        f"for source '{query.source_type.value}'."
                    ),
                    retryable=False,
                    source=query.source_type.value,
                    details={
                        "query_id": query.query_id,
                        "window_seconds": delta_seconds,
                        "maximum_window_seconds": capability.maximum_window_seconds,
                    },
                )
            )

    return errors


class CollectionService(Protocol):
    """Abstract interface for evidence collection per WORK_DIVISION §6.6."""

    def execute(
        self,
        plan: EvidenceQueryPlan,
        catalog: SourceCapabilityCatalog | None = None,
    ) -> RawEvidenceBatch:
        """Execute an EvidenceQueryPlan and return a RawEvidenceBatch."""
        ...


class DefaultCollectionService:
    """Default implementation of CollectionService.

    Owned responsibilities (WORK_DIVISION §6.2, §6.7):
    - Validate query parameters against SourceCapabilityCatalog.
    - Dispatch queries to registered source adapters.
    - Execute independent read-only queries concurrently.
    - Enforce timeouts and limits without crashing on source failure.
    - Aggregate results and structured errors into RawEvidenceBatch.
    - Preserve source-record provenance and timestamps.
    - Never interpret root causes or hypotheses in collection code.
    """

    def __init__(
        self,
        registry: SourceRegistry | None = None,
        clock: Any = None,
        id_generator: Any = None,
        max_workers: int = 4,
        timeout_seconds: float = 10.0,
    ) -> None:
        if registry is not None:
            self.registry = registry
        else:
            from ingestion.capabilities.registry import DefaultSourceRegistry

            self.registry = DefaultSourceRegistry()

        if clock is not None:
            self.clock = clock
        else:
            from ingestion.alert.clock import SystemClock

            self.clock = SystemClock()

        if id_generator is not None:
            self.id_generator = id_generator
        else:
            from ingestion.alert.id_gen import UUIDIdentifierGenerator

            self.id_generator = UUIDIdentifierGenerator()

        self.max_workers = max_workers
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        plan: EvidenceQueryPlan,
        catalog: SourceCapabilityCatalog | None = None,
    ) -> RawEvidenceBatch:
        """Execute all queries in the given plan and return a RawEvidenceBatch."""
        # 1. Resolve capability catalog if not explicitly passed
        if catalog is None:
            synth_seed = IncidentSeed(
                incident_id=plan.incident_id,
                external_alert_id=f"alert-{plan.incident_id}",
                service="unknown",
                environment="simulation",
                severity=Severity.INFO,
                detected_at=self.clock.now(),
                received_at=self.clock.now(),
                summary=f"Synthetic seed for plan {plan.plan_id}",
            )
            catalog = self.registry.capabilities(synth_seed)

        caps_by_type = {s.source_type: s for s in catalog.sources}

        results: list[QueryResult] = []
        errors: list[StructuredError] = []
        valid_queries: list[EvidenceQuery] = []

        # 2. Validate all queries upfront
        for query in plan.queries:
            cap = caps_by_type.get(query.source_type)
            validation_errs = validate_query(query, cap)
            if validation_errs:
                errors.extend(validation_errs)
                # Create a placeholder QueryResult with ERROR/UNAVAILABLE status
                status = (
                    SourceStatus.UNAVAILABLE
                    if any(e.code == "SOURCE_UNAVAILABLE" for e in validation_errs)
                    else SourceStatus.ERROR
                )
                results.append(
                    QueryResult(
                        query_id=query.query_id,
                        source_type=query.source_type,
                        source_adapter="validation-gate",
                        source_status=status,
                        truncated=False,
                        records=[],
                        warnings=[e.message for e in validation_errs],
                    )
                )
            else:
                valid_queries.append(query)

        # 3. Execute valid queries concurrently
        if valid_queries:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_workers
            ) as executor:
                future_to_query = {
                    executor.submit(
                        self._execute_single_query, q, caps_by_type.get(q.source_type)
                    ): q
                    for q in valid_queries
                }

                for future in concurrent.futures.as_completed(future_to_query):
                    query = future_to_query[future]
                    try:
                        res, err = future.result(timeout=self.timeout_seconds + 1.0)
                        results.append(res)
                        if err is not None:
                            errors.append(err)
                    except Exception as exc:
                        logger.error(
                            "Unexpected error running query %s: %s",
                            query.query_id,
                            exc,
                        )
                        err_obj = StructuredError(
                            code="SOURCE_ERROR",
                            message=f"Query '{query.query_id}' encountered error: {exc}",
                            retryable=False,
                            source=query.source_type.value,
                            details={"query_id": query.query_id, "error": str(exc)},
                        )
                        errors.append(err_obj)
                        results.append(
                            QueryResult(
                                query_id=query.query_id,
                                source_type=query.source_type,
                                source_adapter="unknown",
                                source_status=SourceStatus.ERROR,
                                truncated=False,
                                records=[],
                                warnings=[str(exc)],
                            )
                        )

        # Sort results deterministically by query_id
        results.sort(key=lambda r: r.query_id)
        errors.sort(key=lambda e: e.details.get("query_id", e.code))

        batch_id = self.id_generator.generate(prefix="batch")
        collected_at = self.clock.now()

        return RawEvidenceBatch(
            schema_version="1.0",
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            batch_id=batch_id,
            collected_at=collected_at,
            results=results,
            errors=errors,
        )

    def _execute_single_query(
        self, query: EvidenceQuery, capability: SourceCapability | None
    ) -> tuple[QueryResult, StructuredError | None]:
        """Execute a single query against its registered adapter with timeout protection."""
        adapter = getattr(self.registry, "get_source", lambda _: None)(
            query.source_type
        )
        if adapter is None:
            err = StructuredError(
                code="SOURCE_UNAVAILABLE",
                message=f"No adapter registered for source type '{query.source_type.value}'.",
                retryable=True,
                source=query.source_type.value,
                details={"query_id": query.query_id},
            )
            res = QueryResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter="unregistered",
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=[err.message],
            )
            return res, err

        try:
            # Query the adapter
            res: QueryResult = adapter.query(query)

            # Check if adapter returned an error/unavailable status
            err_obj = None
            if res.source_status == SourceStatus.UNAVAILABLE:
                err_obj = StructuredError(
                    code="SOURCE_UNAVAILABLE",
                    message=f"Source '{query.source_type.value}' reported unavailable.",
                    retryable=True,
                    source=query.source_type.value,
                    details={"query_id": query.query_id},
                )
            elif res.source_status == SourceStatus.TIMEOUT:
                err_obj = StructuredError(
                    code="SOURCE_TIMEOUT",
                    message=f"Source '{query.source_type.value}' query timed out.",
                    retryable=True,
                    source=query.source_type.value,
                    details={"query_id": query.query_id},
                )
            elif res.source_status == SourceStatus.ERROR:
                err_obj = StructuredError(
                    code="SOURCE_ERROR",
                    message=f"Source '{query.source_type.value}' reported error.",
                    retryable=False,
                    source=query.source_type.value,
                    details={"query_id": query.query_id},
                )

            return res, err_obj

        except Exception as exc:
            err = StructuredError(
                code="SOURCE_ERROR",
                message=f"Adapter execution failed for query '{query.query_id}': {exc}",
                retryable=False,
                source=query.source_type.value,
                details={"query_id": query.query_id, "error": str(exc)},
            )
            res = QueryResult(
                query_id=query.query_id,
                source_type=query.source_type,
                source_adapter=getattr(adapter, "adapter_name", "unknown"),
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[str(exc)],
            )
            return res, err
