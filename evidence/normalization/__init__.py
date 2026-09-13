"""Canonical, deterministic normalization of source-neutral raw evidence."""

from evidence.normalization.classifiers import (
    ChangeClassifier,
    ClassifiedRecord,
    ClassifierRegistry,
    ConfigurationClassifier,
    DeploymentClassifier,
    LogClassifier,
    MetricClassifier,
    PipelineClassifier,
    SourceClassifier,
    default_classifier_registry,
)
from evidence.normalization.identity import (
    ConfigurationIdentityResolver,
    IdentityResolution,
    IdentityResolver,
)
from evidence.normalization.models import (
    BatchNormalizationError,
    BatchNormalizationResult,
    NormalizedEvidenceCandidate,
    NormalizedTimestamps,
)
from evidence.normalization.service import EvidenceNormalizationService
from evidence.normalization.timestamps import TimestampNormalizer

__all__ = [
    "BatchNormalizationError",
    "BatchNormalizationResult",
    "ChangeClassifier",
    "ClassifiedRecord",
    "ClassifierRegistry",
    "ConfigurationClassifier",
    "ConfigurationIdentityResolver",
    "DeploymentClassifier",
    "EvidenceNormalizationService",
    "IdentityResolution",
    "IdentityResolver",
    "LogClassifier",
    "MetricClassifier",
    "NormalizedEvidenceCandidate",
    "NormalizedTimestamps",
    "PipelineClassifier",
    "SourceClassifier",
    "TimestampNormalizer",
    "default_classifier_registry",
]
