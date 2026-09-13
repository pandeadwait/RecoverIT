"""Raw-payload hashing and provenance construction."""

from __future__ import annotations

from contracts.common import canonical_bytes
from contracts.evidence import EvidenceProvenance
from contracts.primitives import PayloadHasher, Sha256PayloadHasher
from evidence.normalization.models import NormalizedEvidenceCandidate


class ProvenanceBuilder:
    def __init__(self, hasher: PayloadHasher | None = None) -> None:
        self._hasher = hasher or Sha256PayloadHasher()

    def build(self, candidate: NormalizedEvidenceCandidate) -> EvidenceProvenance:
        payload_bytes = canonical_bytes(candidate.raw_payload)
        try:
            digest = self._hasher.digest(payload_bytes)
        finally:
            # The canonical unredacted byte representation is not retained.
            del payload_bytes
        return EvidenceProvenance(
            batch_id=candidate.batch_id,
            query_id=candidate.query_id,
            source_record_id=candidate.source_record_id,
            source_adapter=candidate.source_adapter,
            raw_payload_hash=digest,
        )
