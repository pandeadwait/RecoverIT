"""Source classifier port, registry, and six deterministic classifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from contracts.collection import RawEvidenceRecord
from contracts.common import freeze_json, require_string, thaw_json
from evidence.normalization.models import RecordNormalizationError


@dataclass(frozen=True, slots=True)
class ClassifiedRecord:
    evidence_type: str
    summary: str
    attributes: Any
    service: str | None
    resource: str | None

    def __post_init__(self) -> None:
        require_string(self.evidence_type, "evidence_type")
        require_string(self.summary, "summary")
        object.__setattr__(self, "attributes", freeze_json(self.attributes, "attributes"))


class SourceClassifier(Protocol):
    source_type: str

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord: ...


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())


def _summary(value: str, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise RecordNormalizationError("record summary must not be blank")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _identity(payload: Mapping[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = _text(payload.get(name))
        if value is not None:
            return value
    return None


def _common_identity(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    service = _identity(payload, ("service", "service_id", "application"))
    resource = _identity(payload, ("resource", "resource_id", "host", "pod"))
    return service, resource


def _selected(payload: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: thaw_json(payload[name]) for name in fields if name in payload}


def _stable_external_id(
    raw_record: RawEvidenceRecord, payload: Mapping[str, Any], names: tuple[str, ...]
) -> str | None:
    return _identity(payload, names) or raw_record.source_record_id


class LogClassifier:
    source_type = "logs"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        message = _text(payload.get("message"))
        if message is None:
            raise RecordNormalizationError("log payload requires a message")
        level = (_text(payload.get("level")) or "unknown").casefold()
        evidence_type = "error_event" if level in {"error", "critical", "fatal"} else "log_event"
        service, resource = _common_identity(payload)
        attributes = _selected(
            payload, ("level", "logger", "error_signature", "exception_type", "count")
        )
        attributes["level"] = level
        return ClassifiedRecord(
            evidence_type=evidence_type,
            summary=_summary(message),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class MetricClassifier:
    source_type = "metrics"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        name = _identity(payload, ("metric", "metric_name", "name"))
        if name is None or not ({"value", "values"} & set(payload)):
            raise RecordNormalizationError("metric payload requires a name and value(s)")
        values = thaw_json(payload.get("values"))
        if values is not None:
            if (
                not isinstance(values, list)
                or not values
                or any(isinstance(item, (dict, list)) or item is None for item in values)
            ):
                raise RecordNormalizationError(
                    "metric values must be a non-empty array of JSON scalars"
                )
            value = values[-1]
        else:
            value = thaw_json(payload.get("value"))
            if isinstance(value, (dict, list)) or value is None:
                raise RecordNormalizationError("metric value must be a JSON scalar")
        unit = _text(payload.get("unit"))
        service, resource = _common_identity(payload)
        display = f"Metric {name} = {value}"
        if unit is not None:
            display += f" {unit}"
        attributes = {"metric_name": name, "value": value}
        if values is not None:
            attributes["values"] = values
        if unit is not None:
            attributes["unit"] = unit
        if "baseline" in payload:
            attributes["baseline"] = thaw_json(payload["baseline"])
        if "dimensions" in payload:
            attributes["dimensions"] = thaw_json(payload["dimensions"])
        return ClassifiedRecord(
            evidence_type="metric_observation",
            summary=_summary(display),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class ChangeClassifier:
    source_type = "changes"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        revision = _stable_external_id(
            raw_record, payload, ("revision", "commit_sha", "change_id")
        )
        if revision is None:
            raise RecordNormalizationError("change payload requires a stable revision or record ID")
        title = _identity(payload, ("title", "message", "summary"))
        service, resource = _common_identity(payload)
        attributes = _selected(payload, ("author", "status", "files_changed", "repository"))
        attributes["revision"] = revision
        return ClassifiedRecord(
            evidence_type="source_change",
            summary=_summary(title or f"Source change {revision}"),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class DeploymentClassifier:
    source_type = "deployments"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        deployment_id = _stable_external_id(
            raw_record, payload, ("deployment_id", "id")
        )
        if deployment_id is None:
            raise RecordNormalizationError("deployment payload requires a stable ID")
        status = _text(payload.get("status")) or "unknown"
        service, resource = _common_identity(payload)
        attributes = _selected(
            payload, ("revision", "commit_sha", "artifact_version", "environment", "status")
        )
        attributes["deployment_id"] = deployment_id
        attributes["status"] = status.casefold()
        return ClassifiedRecord(
            evidence_type="deployment_event",
            summary=_summary(f"Deployment {deployment_id} {status.casefold()}"),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class PipelineClassifier:
    source_type = "pipelines"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        run_id = _stable_external_id(raw_record, payload, ("run_id", "pipeline_run_id", "id"))
        if run_id is None:
            raise RecordNormalizationError("pipeline payload requires a stable run ID")
        status = _text(payload.get("status")) or "unknown"
        service, resource = _common_identity(payload)
        attributes = _selected(
            payload, ("revision", "commit_sha", "pipeline", "stage", "status", "result")
        )
        attributes["pipeline_run_id"] = run_id
        attributes["status"] = status.casefold()
        return ClassifiedRecord(
            evidence_type="pipeline_event",
            summary=_summary(f"Pipeline run {run_id} {status.casefold()}"),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class ConfigurationClassifier:
    source_type = "configuration"

    def classify(
        self, raw_record: RawEvidenceRecord, payload: Mapping[str, Any]
    ) -> ClassifiedRecord:
        change_id = _stable_external_id(raw_record, payload, ("change_id", "id", "revision"))
        if change_id is None:
            raise RecordNormalizationError("configuration payload requires a stable change ID")
        keys = payload.get("keys", payload.get("key"))
        service, resource = _common_identity(payload)
        attributes = _selected(payload, ("revision", "environment", "actor"))
        attributes["configuration_change_id"] = change_id
        if keys is not None:
            attributes["keys"] = thaw_json(keys)
        label = _text(payload.get("summary")) or f"Configuration change {change_id}"
        return ClassifiedRecord(
            evidence_type="configuration_change",
            summary=_summary(label),
            attributes=attributes,
            service=service,
            resource=resource,
        )


class ClassifierRegistry:
    """Immutable source-type routing independent of collector implementations."""

    def __init__(self, classifiers: tuple[SourceClassifier, ...]) -> None:
        by_source: dict[str, SourceClassifier] = {}
        for classifier in classifiers:
            if classifier.source_type in by_source:
                raise ValueError(f"duplicate classifier for {classifier.source_type!r}")
            by_source[classifier.source_type] = classifier
        self._by_source = by_source

    def get(self, source_type: str) -> SourceClassifier:
        try:
            return self._by_source[source_type]
        except KeyError as error:
            raise RecordNormalizationError(
                f"no classifier registered for source type {source_type!r}"
            ) from error


def default_classifier_registry() -> ClassifierRegistry:
    return ClassifierRegistry(
        (
            LogClassifier(),
            MetricClassifier(),
            ChangeClassifier(),
            DeploymentClassifier(),
            PipelineClassifier(),
            ConfigurationClassifier(),
        )
    )
