"""Capabilities discovery subpackage exports."""

from ingestion.capabilities.registry import (
    DEFAULT_CAPABILITY_SPECS,
    DefaultSourceRegistry,
    SourceRegistry,
    get_default_capability,
)

__all__ = [
    "SourceRegistry",
    "DefaultSourceRegistry",
    "DEFAULT_CAPABILITY_SPECS",
    "get_default_capability",
]
