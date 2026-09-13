"""Context repository port and reference adapter."""

from evidence.context.repositories.memory import InMemoryContextRepository
from evidence.context.repositories.ports import ContextRepository

__all__ = ["ContextRepository", "InMemoryContextRepository"]
