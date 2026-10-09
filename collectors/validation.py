"""Query validation for operational evidence collection.

Validates an EvidenceQuery against the corresponding SourceCapability
before dispatching to source adapters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from contracts.collection.schemas import EvidenceQuery, SourceCapability
from contracts.errors.schemas import StructuredError


def _parse_time_value(val: Any) -> datetime | None:
    """Parse a datetime instance or ISO string into a timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            clean = val.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            return None
    return None


def validate_query(
    query: EvidenceQuery,
    capability: SourceCapability | None,
) -> list[StructuredError]:
    """Validate a single query against a source capability descriptor.

    Enforces:
    - Source must be available in catalog.
    - Parameters must only use supported query fields.
    - Time-window must not exceed maximum_window_seconds.
    - Limit parameter must not exceed maximum_items.
    """
    errors: list[StructuredError] = []
    source_name = str(query.source_type.value if hasattr(query.source_type, "value") else query.source_type)

    # 1. Source availability check
    if capability is None or not capability.available:
        reason = capability.unavailable_reason if capability and capability.unavailable_reason else "Source is unavailable or not registered"
        errors.append(
            StructuredError(
                code="SOURCE_UNAVAILABLE",
                message=f"Source type '{source_name}' is unavailable: {reason}",
                retryable=True,
                stage="collect_evidence",
                source_type=query.source_type,
                details={
                    "query_id": query.query_id,
                    "reason": reason,
                },
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
                        f"'{source_name}': {sorted(unsupported)}. "
                        f"Supported fields: {sorted(capability.supported_query_fields)}."
                    ),
                    retryable=False,
                    stage="collect_evidence",
                    source_type=query.source_type,
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
                        f"for source '{source_name}'."
                    ),
                    retryable=False,
                    stage="collect_evidence",
                    source_type=query.source_type,
                    details={
                        "query_id": query.query_id,
                        "window_seconds": delta_seconds,
                        "maximum_window_seconds": capability.maximum_window_seconds,
                    },
                )
            )

    # 4. Result limit validation
    limit_val = query.parameters.get("limit") or query.parameters.get("max_commits") or query.parameters.get("max_items")
    if limit_val is not None:
        try:
            limit_int = int(limit_val)
            if limit_int > capability.maximum_items:
                errors.append(
                    StructuredError(
                        code="QUERY_LIMIT_EXCEEDED",
                        message=(
                            f"Query '{query.query_id}' requested limit ({limit_int}) "
                            f"exceeds maximum allowed items ({capability.maximum_items}) "
                            f"for source '{source_name}'."
                        ),
                        retryable=False,
                        stage="collect_evidence",
                        source_type=query.source_type,
                        details={
                            "query_id": query.query_id,
                            "requested_limit": limit_int,
                            "maximum_items": capability.maximum_items,
                        },
                    )
                )
        except (ValueError, TypeError):
            pass

    return errors
