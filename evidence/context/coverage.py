"""Deterministic source-coverage calculation from collection contracts."""

from __future__ import annotations

from typing import Mapping

from contracts.collection import RawEvidenceBatch, SourceResult


LEGACY_SOURCE_TYPES = (
    "logs",
    "metrics",
    "changes",
    "deployments",
    "pipelines",
    "configuration",
)

CANONICAL_SOURCE_TYPES = (
    "logs",
    "metrics",
    "changes",
    "deployments",
    "pipelines",
    "configuration",
    "health",
)

SOURCE_TYPES = LEGACY_SOURCE_TYPES


class SourceCoverageCalculator:
    """Distinguishes available, empty, partial, unavailable, and not-queried sources."""

    def calculate(
        self,
        batches: tuple[RawEvidenceBatch, ...],
        previous_coverage: Mapping[str, str | Any] | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> dict[str, str]:
        if source_types is None:
            has_health_batch = any(
                str(getattr(r.source_type, "value", r.source_type)).lower() == "health"
                for batch in batches
                for r in batch.results
            )
            has_health_prev = bool(previous_coverage and "health" in previous_coverage)
            if has_health_batch or has_health_prev:
                active_sources = CANONICAL_SOURCE_TYPES
            else:
                active_sources = SOURCE_TYPES
        else:
            active_sources = source_types

        results_by_source: dict[str, list[SourceResult]] = {
            source_type: [] for source_type in active_sources
        }
        for batch in batches:
            for result in batch.results:
                st = (
                    result.source_type.value
                    if hasattr(result.source_type, "value")
                    else str(result.source_type)
                )
                if st in results_by_source:
                    results_by_source[st].append(result)

        coverage: dict[str, str] = {}
        for source_type in active_sources:
            results = results_by_source[source_type]
            if not results:
                prev_val = (
                    previous_coverage.get(source_type)
                    if previous_coverage is not None
                    else None
                )
                if prev_val is not None:
                    prev_str = prev_val.value if hasattr(prev_val, "value") else str(prev_val)
                    coverage[source_type] = prev_str
                else:
                    coverage[source_type] = "not_queried"
            elif any(result.records for result in results):
                if active_sources == CANONICAL_SOURCE_TYPES and any(
                    str(getattr(result.source_status, "value", result.source_status)).lower() == "partial"
                    for result in results
                ):
                    coverage[source_type] = "partial"
                else:
                    coverage[source_type] = "available"
            elif any(
                str(getattr(result.source_status, "value", result.source_status)).lower() in {"ok", "partial", "empty"}
                for result in results
            ):
                coverage[source_type] = "empty"
            else:
                coverage[source_type] = "unavailable"
        return coverage
