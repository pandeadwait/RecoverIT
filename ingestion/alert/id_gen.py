"""Identifier generator abstractions for predictable and unique IDs."""

from __future__ import annotations

import uuid
from typing import Protocol


class IdentifierGenerator(Protocol):
    """Abstract generator for unique identifiers."""

    def generate(self, prefix: str = "inc") -> str:
        """Generate a unique identifier with the given prefix."""
        ...


class UUIDIdentifierGenerator:
    """Default identifier generator using UUID4."""

    def generate(self, prefix: str = "inc") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DeterministicIdentifierGenerator:
    """Sequential identifier generator for reproducible testing."""

    def __init__(self, prefix: str = "inc", start: int = 1) -> None:
        self._counter = start
        self._default_prefix = prefix

    def generate(self, prefix: str | None = None) -> str:
        p = prefix or self._default_prefix
        val = f"{p}_{self._counter:03d}"
        self._counter += 1
        return val
