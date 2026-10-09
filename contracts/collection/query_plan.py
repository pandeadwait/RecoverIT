"""Compatibility imports for the canonical collection query contracts.

New code imports from :mod:`contracts.collection.schemas`. This module remains
temporarily so pre-migration consumers do not define or deserialize a second
query-plan shape.
"""

from contracts.collection.schemas import EvidenceQuery, EvidenceQueryPlan

__all__ = ["EvidenceQuery", "EvidenceQueryPlan"]
