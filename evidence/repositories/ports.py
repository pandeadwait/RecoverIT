"""Domain-owned evidence persistence contracts.

Adapters must preserve these semantics so application code never needs a
database query type, exception, cursor, or ordering rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from contracts.common import require_int, require_string
from contracts.errors import ProcessingError
from contracts.evidence import EvidenceFilter, EvidenceRecord


class RepositoryError(RuntimeError):
    """Base error translated at a storage adapter boundary."""

    code = "storage_failure"
    retryable = True

    def __init__(self, message: str, *, details: object | None = None) -> None:
        self.message = require_string(message, "repository_error.message")
        self.details = details
        super().__init__(self.message)

    def to_processing_error(self) -> ProcessingError:
        return ProcessingError(
            schema_version="1.0",
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
        )


class RepositoryConflictError(RepositoryError):
    """An immutable identity is already bound to different content."""

    code = "x-storage-conflict"
    retryable = False


class RepositoryValidationError(RepositoryError):
    """A write would violate incident or revision integrity."""

    code = "x-storage-validation"
    retryable = False


class EvidenceOrder(StrEnum):
    """Portable evidence ordering, always with evidence ID as the final tie-breaker."""

    EVENT_TIME_ASC = "event_time_asc"
    EVENT_TIME_DESC = "event_time_desc"


@dataclass(frozen=True, slots=True)
class EvidencePageRequest:
    """Cursor pagination input owned by the domain rather than a database."""

    limit: int = 100
    cursor: str | None = None
    order: EvidenceOrder = EvidenceOrder.EVENT_TIME_ASC

    def __post_init__(self) -> None:
        require_int(self.limit, "page.limit", minimum=1)
        if self.limit > 1000:
            raise ValueError("page.limit must not exceed 1000")
        if self.cursor is not None:
            require_string(self.cursor, "page.cursor")
        if not isinstance(self.order, EvidenceOrder):
            raise ValueError("page.order must be an EvidenceOrder")


@dataclass(frozen=True, slots=True)
class EvidencePage:
    items: tuple[EvidenceRecord, ...]
    next_cursor: str | None


class EvidenceRepository(Protocol):
    """Persistence port for immutable canonical evidence.

    ``save_all`` is atomic. Saving an identical existing record is idempotent;
    binding the same evidence ID to different content raises
    ``RepositoryConflictError``.
    """

    def save_all(self, records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]: ...

    def get(self, evidence_id: str) -> EvidenceRecord | None: ...

    def query(self, evidence_filter: EvidenceFilter) -> tuple[EvidenceRecord, ...]: ...

    def query_page(
        self, evidence_filter: EvidenceFilter, page: EvidencePageRequest
    ) -> EvidencePage: ...
