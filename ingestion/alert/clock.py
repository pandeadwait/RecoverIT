"""Clock abstractions for injectable, deterministic time handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """Abstract clock for retrieving the current time."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...


class SystemClock:
    """System clock that returns the current UTC time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Fixed or controllable clock for deterministic testing."""

    def __init__(self, fixed_time: datetime) -> None:
        if fixed_time.tzinfo is None:
            fixed_time = fixed_time.replace(tzinfo=timezone.utc)
        self._time = fixed_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            new_time = new_time.replace(tzinfo=timezone.utc)
        self._time = new_time

    def advance(self, delta: timedelta) -> None:
        self._time += delta

    def now(self) -> datetime:
        return self._time
