"""Replaceable deterministic evidence-ID strategy."""

from __future__ import annotations

from typing import Mapping, Protocol

from contracts.common import thaw_json
from contracts.primitives import DeterministicIdGenerator, IdGenerator
from evidence.security.models import SecuredEvidenceCandidate


_EXTERNAL_ID_FIELDS = {
    "changes": "revision",
    "deployments": "deployment_id",
    "pipelines": "pipeline_run_id",
    "configuration": "configuration_change_id",
}


class EvidenceIdStrategy(Protocol):
    def create_id(
        self,
        candidate: SecuredEvidenceCandidate,
        *,
        aggregation_key_hash: str | None = None,
    ) -> str: ...

    def external_id(self, candidate: SecuredEvidenceCandidate) -> str | None: ...


class DeterministicEvidenceIdStrategy(EvidenceIdStrategy):
    """Derives IDs from stable source identity, never collection batch metadata."""

    def __init__(self, generator: IdGenerator | None = None) -> None:
        self._generator = generator or DeterministicIdGenerator()

    def external_id(self, candidate: SecuredEvidenceCandidate) -> str | None:
        field = _EXTERNAL_ID_FIELDS.get(candidate.source_type)
        if field is None:
            return None
        attributes = thaw_json(candidate.attributes)
        if not isinstance(attributes, Mapping):
            return None
        value = attributes.get(field)
        return value if isinstance(value, str) and value.strip() else None

    def create_id(
        self,
        candidate: SecuredEvidenceCandidate,
        *,
        aggregation_key_hash: str | None = None,
    ) -> str:
        external_id = self.external_id(candidate)
        if aggregation_key_hash is not None:
            material = {
                "incident_id": candidate.incident_id,
                "source_type": candidate.source_type,
                "aggregation_key_hash": aggregation_key_hash,
            }
        elif external_id is not None:
            material = {
                "incident_id": candidate.incident_id,
                "source_type": candidate.source_type,
                "external_id": external_id,
            }
        else:
            material = {
                "incident_id": candidate.incident_id,
                "source_type": candidate.source_type,
                "source_record_id": candidate.provenance.source_record_id,
                "raw_payload_hash": candidate.provenance.raw_payload_hash,
            }
        return self._generator.create("ev", material)
