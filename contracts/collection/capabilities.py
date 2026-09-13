"""SourceCapabilityCatalog — runtime declaration of available sources.

Tells Person 3 what each data source can provide so evidence queries
use only advertised capabilities.  See WORK_DIVISION §6.5.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel

from contracts.enums import SourceType


class SourceCapability(BaseModel):
    """Capability descriptor for a single data source."""

    source_type: SourceType
    available: bool
    supported_query_fields: list[str]
    maximum_window_seconds: int
    maximum_items: int


class SourceCapabilityCatalog(BaseModel):
    """Catalog of all source capabilities for an incident."""

    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    generated_at: AwareDatetime
    sources: list[SourceCapability]
