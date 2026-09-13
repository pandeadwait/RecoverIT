"""Standard capability specifications and factory for operational sources."""

from __future__ import annotations

from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceType

DEFAULT_CAPABILITY_SPECS: dict[SourceType, dict] = {
    SourceType.LOGS: {
        "supported_query_fields": [
            "service",
            "start_time",
            "end_time",
            "severity",
            "pattern",
            "limit",
        ],
        "maximum_window_seconds": 86400,
        "maximum_items": 1000,
    },
    SourceType.METRICS: {
        "supported_query_fields": [
            "service",
            "metric_name",
            "start_time",
            "end_time",
            "aggregation",
        ],
        "maximum_window_seconds": 86400,
        "maximum_items": 1000,
    },
    SourceType.CHANGES: {
        "supported_query_fields": [
            "repository",
            "since",
            "until",
            "paths",
            "max_commits",
        ],
        "maximum_window_seconds": 604800,
        "maximum_items": 200,
    },
    SourceType.DEPLOYMENTS: {
        "supported_query_fields": [
            "service",
            "since",
            "until",
            "limit",
        ],
        "maximum_window_seconds": 604800,
        "maximum_items": 100,
    },
    SourceType.PIPELINES: {
        "supported_query_fields": [
            "pipeline",
            "since",
            "until",
            "limit",
        ],
        "maximum_window_seconds": 604800,
        "maximum_items": 100,
    },
    SourceType.CONFIGURATION: {
        "supported_query_fields": [
            "service",
            "start_time",
            "end_time",
            "keys",
        ],
        "maximum_window_seconds": 604800,
        "maximum_items": 200,
    },
}


def get_default_capability(
    source_type: SourceType, available: bool = True
) -> SourceCapability:
    """Return a standard SourceCapability descriptor for the given source type."""
    spec = DEFAULT_CAPABILITY_SPECS.get(source_type, {})
    if not available:
        return SourceCapability(
            source_type=source_type,
            available=False,
            supported_query_fields=[],
            maximum_window_seconds=0,
            maximum_items=0,
        )
    return SourceCapability(
        source_type=source_type,
        available=True,
        supported_query_fields=list(spec.get("supported_query_fields", [])),
        maximum_window_seconds=spec.get("maximum_window_seconds", 86400),
        maximum_items=spec.get("maximum_items", 100),
    )
