"""Deterministic source-coverage calculation from collection contracts."""

from __future__ import annotations

from typing import Mapping

from contracts.collection import RawEvidenceBatch, SourceResult


SOURCE_TYPES = (
    "logs",
    "metrics",
    "changes",
    "deployments",
    "pipelines",
    "configuration",
)


class SourceCoverageCalculator:
    """Distinguishes available, empty, unavailable, and not-queried sources."""

    def calculate(
        self,
        batches: tuple[RawEvidenceBatch, ...],
        previous_coverage: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        results_by_source: dict[str, list[SourceResult]] = {
            source_type: [] for source_type in SOURCE_TYPES
        }
        for batch in batches:
            for result in batch.results:
                if result.source_type in results_by_source:
                    results_by_source[result.source_type].append(result)

        coverage: dict[str, str] = {}
        for source_type in SOURCE_TYPES:
            results = results_by_source[source_type]
            if not results:
                previous = (
                    previous_coverage.get(source_type)
                    if previous_coverage is not None
                    else None
                )
                coverage[source_type] = previous or "not_queried"
            elif any(result.records for result in results):
                coverage[source_type] = "available"
            elif any(result.source_status in {"ok", "partial"} for result in results):
                coverage[source_type] = "empty"
            else:
                coverage[source_type] = "unavailable"
        return coverage
