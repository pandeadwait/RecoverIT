"""Explainable reliability, freshness, truncation, and skew rules."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from contracts.common import SCHEMA_VERSION, require_string
from contracts.errors import ProcessingWarning
from contracts.evidence import EvidenceQuality
from contracts.primitives import Clock, SystemClock
from evidence.normalization.models import NormalizedEvidenceCandidate


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    quality: EvidenceQuality
    rationale: str
    warnings: tuple[ProcessingWarning, ...] = ()


class QualityPolicy(Protocol):
    def assess(
        self, candidate: NormalizedEvidenceCandidate, redactions_applied: bool
    ) -> QualityAssessment: ...


class ConfigurableQualityPolicy(QualityPolicy):
    """Reference policy whose reliability choices are entirely configuration."""

    _DEGRADED = {"high": "medium", "medium": "low", "low": "low", "unknown": "unknown"}

    def __init__(
        self,
        *,
        reliability_by_source: Mapping[str, str] | None = None,
        reliability_by_evidence_type: Mapping[str, str] | None = None,
        default_reliability: str = "unknown",
        freshness_reference: str = "collected_at",
        degrade_truncated: bool = True,
        clock: Clock | None = None,
    ) -> None:
        if freshness_reference not in {"collected_at", "clock"}:
            raise ValueError("freshness_reference must be 'collected_at' or 'clock'")
        # EvidenceQuality is the shared validator for reliability extension values.
        EvidenceQuality(default_reliability, None, False, False)
        self._source_rules = self._validated_rules(reliability_by_source or {})
        self._type_rules = self._validated_rules(reliability_by_evidence_type or {})
        self._default = default_reliability
        self._freshness_reference = freshness_reference
        self._degrade_truncated = degrade_truncated
        self._clock = clock or SystemClock()

    @staticmethod
    def _validated_rules(values: Mapping[str, str]) -> Mapping[str, str]:
        result: dict[str, str] = {}
        for key, reliability in values.items():
            normalized_key = require_string(key, "quality.rule").strip()
            EvidenceQuality(reliability, None, False, False)
            result[normalized_key] = reliability
        return MappingProxyType(result)

    def assess(
        self, candidate: NormalizedEvidenceCandidate, redactions_applied: bool
    ) -> QualityAssessment:
        if candidate.evidence_type in self._type_rules:
            reliability = self._type_rules[candidate.evidence_type]
            rule = f"evidence_type:{candidate.evidence_type}"
        elif candidate.source_type in self._source_rules:
            reliability = self._source_rules[candidate.source_type]
            rule = f"source_type:{candidate.source_type}"
        else:
            reliability = self._default
            rule = "default"

        truncated_degradation = candidate.source_truncated and self._degrade_truncated
        if truncated_degradation:
            reliability = self._DEGRADED.get(reliability, reliability)

        signal_time = candidate.timestamps.observed_at or candidate.timestamps.event_time
        reference = (
            candidate.timestamps.collected_at
            if self._freshness_reference == "collected_at"
            else self._clock.now()
        )
        warnings: list[ProcessingWarning] = []
        freshness: int | None = None
        if signal_time is not None:
            seconds = int((reference - signal_time).total_seconds())
            if seconds < 0:
                freshness = 0
                warnings.append(
                    ProcessingWarning(
                        schema_version=SCHEMA_VERSION,
                        code="x-clock-skew",
                        message="evidence timestamp is later than the freshness reference",
                        record_id=candidate.source_record_id,
                        details={"skew_seconds": abs(seconds)},
                    )
                )
            else:
                freshness = seconds

        rationale = f"reliability selected by {rule}"
        if truncated_degradation:
            rationale += "; degraded one level because the source result was truncated"
        rationale += f"; freshness referenced to {self._freshness_reference}"
        return QualityAssessment(
            quality=EvidenceQuality(
                reliability=reliability,
                freshness_seconds=freshness,
                truncated_source=candidate.source_truncated,
                redactions_applied=redactions_applied,
            ),
            rationale=rationale,
            warnings=tuple(warnings),
        )
