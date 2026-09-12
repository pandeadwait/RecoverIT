"""Timestamp normalization with explicit uncertainty and original metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any

from contracts.collection import RawEvidenceRecord
from evidence.normalization.models import NormalizedTimestamps, RecordNormalizationError


def _original(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _offset(value: datetime | None) -> str | None:
    if value is None or value.utcoffset() is None:
        return None
    seconds = int(value.utcoffset().total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


class TimestampNormalizer:
    """Normalizes only explicit source timestamps; it never fabricates one."""

    def normalize(
        self,
        raw_record: RawEvidenceRecord,
        collected_at: datetime,
        payload: Mapping[str, Any],
    ) -> NormalizedTimestamps:
        approximate = payload.get("event_time_approximate", False)
        if not isinstance(approximate, bool):
            raise RecordNormalizationError("event_time_approximate must be a boolean")
        uncertainty = payload.get("time_uncertainty_ms")
        if uncertainty is not None and (
            isinstance(uncertainty, bool) or not isinstance(uncertainty, int) or uncertainty < 0
        ):
            raise RecordNormalizationError("time_uncertainty_ms must be a non-negative integer")
        if collected_at.tzinfo is None or collected_at.utcoffset() is None:
            raise RecordNormalizationError("collected_at must include timezone information")
        return NormalizedTimestamps(
            event_time=(
                None
                if raw_record.event_time is None
                else raw_record.event_time.astimezone(timezone.utc)
            ),
            observed_at=(
                None
                if raw_record.observed_at is None
                else raw_record.observed_at.astimezone(timezone.utc)
            ),
            collected_at=collected_at.astimezone(timezone.utc),
            event_time_original=_original(raw_record.event_time),
            observed_at_original=_original(raw_record.observed_at),
            event_time_offset=_offset(raw_record.event_time),
            observed_at_offset=_offset(raw_record.observed_at),
            event_time_approximate=approximate,
            time_uncertainty_ms=uncertainty,
        )
