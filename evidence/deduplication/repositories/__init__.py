"""Deduplication-state repository port and reference adapter."""

from evidence.deduplication.repositories.memory import (
    InMemoryDeduplicationStateRepository,
)
from evidence.deduplication.repositories.ports import DeduplicationStateRepository

__all__ = [
    "DeduplicationStateRepository",
    "InMemoryDeduplicationStateRepository",
]
