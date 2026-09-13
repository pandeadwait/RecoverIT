"""Tool Gateway subpackage — collection service and query execution."""

from collectors.gateway.collection_service import (
    CollectionService,
    DefaultCollectionService,
    validate_query,
)

__all__ = [
    "CollectionService",
    "DefaultCollectionService",
    "validate_query",
]
