"""Deterministic ports and reference primitives used by later phases."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Protocol

from contracts.common import canonical_bytes, require_identifier


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def create(self, prefix: str, material: object) -> str: ...


class PayloadHasher(Protocol):
    def digest(self, payload: bytes) -> str: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware instant")
        self._instant = instant.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._instant


class DeterministicIdGenerator:
    """Reference ID generator; applications may replace it through the IdGenerator port."""

    def create(self, prefix: str, material: object) -> str:
        require_identifier(prefix, "prefix")
        digest = hashlib.sha256(canonical_bytes(material)).hexdigest()[:24]
        return f"{prefix}_{digest}"


class Sha256PayloadHasher:
    """Reference hasher; only the digest string crosses the contract boundary."""

    def digest(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"
