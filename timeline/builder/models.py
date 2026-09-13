"""Timeline-builder configuration and validation errors."""

from __future__ import annotations

from dataclasses import dataclass


class TimelineBuildError(ValueError):
    """Evidence cannot form a reference-valid deterministic timeline."""


@dataclass(frozen=True, slots=True)
class TimelineRuleConfiguration:
    """Explicit deterministic rules for temporal relationship construction."""

    coincidence_window_ms: int = 1_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.coincidence_window_ms, bool)
            or not isinstance(self.coincidence_window_ms, int)
            or self.coincidence_window_ms < 0
        ):
            raise ValueError("coincidence_window_ms must be a non-negative integer")
