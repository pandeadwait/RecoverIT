"""Base class for all fixture adapters providing deterministic test data."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from collectors.fixtures.data_loader import load_scenario_json, load_scenario_records
from collectors.interfaces import SourceQuery, SourceResult
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.enums import SourceStatus, SourceType
from collectors.specs import get_default_capability
from contracts.incident.seed import IncidentSeed


class BaseFixtureAdapter:
    """Base implementation for fixture-based source adapters.

    Features (WORK_DIVISION §6.2, §6.6, §6.8; CREDIBILITY_IMPROVEMENT_PLAN §3.2):
    - Deterministic responses from recorded scenario fixtures.
    - Parameter filtering for logs, metrics, changes, deployments, pipelines, config.
    - Clean empty result handling with SourceStatus.OK and explicit warning.
    - Configurable availability, timeouts, and error states for resilience testing.
    - Parameter limit enforcement and truncation marking.
    - Typed SourceResult output conforming to shared contracts.
    - Credential-free data guarantee.
    """

    source_type: SourceType
    adapter_name: str = "fixture-adapter"

    def __init__(
        self,
        scenario_name: str | None = None,
        records: list[RawRecord] | None = None,
        available: bool = True,
        simulated_status: SourceStatus = SourceStatus.OK,
        simulated_warnings: list[str] | None = None,
        simulated_error: str | None = None,
        fail_on_capability: bool = False,
    ) -> None:
        self.available = available
        self.simulated_status = simulated_status
        self.simulated_warnings = list(simulated_warnings or [])
        self.simulated_error = simulated_error
        self.fail_on_capability = fail_on_capability
        self._scenario_service: str | None = None

        if records is not None:
            self._records = list(records)
        elif scenario_name is not None:
            try:
                scen_data = load_scenario_json(scenario_name)
                self._scenario_service = scen_data.get("service")
            except Exception:
                self._scenario_service = None
            self._records = load_scenario_records(scenario_name, self.source_type)
        else:
            # Default to bad_db_config baseline
            try:
                scen_data = load_scenario_json("bad_db_config")
                self._scenario_service = scen_data.get("service")
            except Exception:
                self._scenario_service = None
            self._records = load_scenario_records("bad_db_config", self.source_type)

    def get_capability(
        self, incident: IncidentSeed | None = None
    ) -> SourceCapability:
        """Return the capability descriptor for this fixture adapter."""
        if self.fail_on_capability:
            raise ConnectionError(
                f"Cannot communicate with {self.source_type.value} adapter."
            )
        return get_default_capability(self.source_type, available=self.available)

    @staticmethod
    def _parse_time_value(val: Any) -> datetime | None:
        """Parse time values from string, datetime, or epoch number."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
            if val.endswith("Z"):
                val = val[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(val)
                return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _matches_time(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check if record satisfies time window constraints."""
        start_raw = (
            params.get("start_time")
            or params.get("since")
            or params.get("from_time")
        )
        end_raw = (
            params.get("end_time")
            or params.get("until")
            or params.get("to_time")
        )
        if isinstance(params.get("time_range"), dict):
            start_raw = start_raw or params["time_range"].get("start")
            end_raw = end_raw or params["time_range"].get("end")

        start_dt = self._parse_time_value(start_raw)
        end_dt = self._parse_time_value(end_raw)

        if start_dt is None and end_dt is None:
            return True

        rec_time = record.event_time or record.observed_at
        if rec_time is None:
            return True

        if start_dt is not None and rec_time < start_dt:
            return False
        if end_dt is not None and rec_time > end_dt:
            return False
        return True

    def _matches_service(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check if record matches service or repository filter."""
        query_svc = params.get("service")
        if self.source_type == SourceType.CHANGES and not query_svc:
            query_svc = params.get("repository")

        if query_svc is None or query_svc == "":
            return True

        payload = record.payload or {}
        record_svc = payload.get("service")
        if record_svc is None:
            if self.source_type == SourceType.CHANGES:
                record_svc = payload.get("repository") or self._scenario_service
            elif self.source_type == SourceType.PIPELINES:
                record_svc = self._scenario_service or payload.get("pipeline")
            else:
                record_svc = self._scenario_service

        if record_svc is None:
            return True

        target = str(record_svc).strip().lower()

        if isinstance(query_svc, (list, tuple, set)):
            allowed = [str(s).strip().lower() for s in query_svc if s]
            return any(a == target or a in target or target in a for a in allowed)

        q_str = str(query_svc).strip().lower()
        return q_str == target or q_str in target or target in q_str

    def _matches_logs(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check log-specific filters (pattern, severity/level)."""
        payload = record.payload or {}

        pattern = (
            params.get("pattern")
            or params.get("regex")
            or params.get("query")
            or params.get("filter")
        )
        if pattern:
            msg = str(payload.get("message", ""))
            err_sig = str(payload.get("error_signature", ""))
            search_text = f"{msg} {err_sig}"
            try:
                if not re.search(str(pattern), search_text, re.IGNORECASE):
                    return False
            except re.error:
                if str(pattern).lower() not in search_text.lower():
                    return False

        severity = (
            params.get("severity")
            or params.get("level")
            or params.get("severities")
            or params.get("levels")
        )
        if severity:
            rec_level = str(
                payload.get("level") or payload.get("severity") or ""
            ).strip().lower()
            if isinstance(severity, (list, tuple, set)):
                allowed_levels = [str(s).strip().lower() for s in severity if s]
                if rec_level not in allowed_levels:
                    return False
            else:
                if rec_level != str(severity).strip().lower():
                    return False

        return True

    def _matches_metrics(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check metric-specific filters (exact metric_name matching)."""
        payload = record.payload or {}

        metric_param = (
            params.get("metric_name")
            or params.get("metric")
            or params.get("metrics")
        )
        if metric_param:
            rec_metric = str(payload.get("metric_name") or "").strip().lower()
            if isinstance(metric_param, (list, tuple, set)):
                allowed = [str(m).strip().lower() for m in metric_param if m]
                if rec_metric not in allowed:
                    return False
            else:
                if rec_metric != str(metric_param).strip().lower():
                    return False

        return True

    def _matches_changes(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check change-specific filters (paths against files_changed)."""
        payload = record.payload or {}

        paths_param = (
            params.get("paths")
            or params.get("path")
            or params.get("files")
            or params.get("files_changed")
        )
        if paths_param:
            if isinstance(paths_param, (list, tuple, set)):
                req_paths = [
                    str(p).replace("\\", "/").strip().lower()
                    for p in paths_param
                    if p
                ]
            else:
                req_paths = [
                    str(paths_param).replace("\\", "/").strip().lower()
                ]

            raw_files = payload.get("files_changed") or []
            rec_files = [
                str(f).replace("\\", "/").strip().lower() for f in raw_files
            ]

            matched = False
            for req in req_paths:
                for rec in rec_files:
                    if (
                        req == rec
                        or rec.endswith("/" + req)
                        or rec.startswith(req + "/")
                        or req in rec
                    ):
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                return False

        return True

    def _matches_deployments(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check deployment-specific filters (status, version/deployment_id)."""
        payload = record.payload or {}

        status_param = params.get("status") or params.get("deployment_status")
        if status_param:
            rec_status = str(payload.get("status", "")).strip().lower()
            if str(status_param).strip().lower() != rec_status:
                return False

        ver_param = params.get("version") or params.get("deployment_id")
        if ver_param:
            rec_ver = str(payload.get("version", "")).strip().lower()
            rec_dep_id = str(payload.get("deployment_id", "")).strip().lower()
            target_ver = str(ver_param).strip().lower()
            if target_ver != rec_ver and target_ver != rec_dep_id:
                return False

        return True

    def _matches_pipelines(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check pipeline-specific filters (pipeline name, status)."""
        payload = record.payload or {}

        pipe_param = params.get("pipeline") or params.get("pipeline_name")
        if pipe_param:
            rec_pipe = str(payload.get("pipeline", "")).strip().lower()
            target_pipe = str(pipe_param).strip().lower()
            if (
                target_pipe != rec_pipe
                and target_pipe not in rec_pipe
                and rec_pipe not in target_pipe
            ):
                return False

        status_param = params.get("status")
        if status_param:
            rec_status = str(payload.get("status", "")).strip().lower()
            if str(status_param).strip().lower() != rec_status:
                return False

        return True

    def _matches_configuration(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Check configuration-specific filters (key/keys)."""
        payload = record.payload or {}

        key_param = (
            params.get("key")
            or params.get("keys")
            or params.get("config_key")
        )
        if key_param:
            rec_key = payload.get("key")
            if rec_key is None:
                return False
            rec_key_str = str(rec_key).strip().lower()
            if isinstance(key_param, (list, tuple, set)):
                allowed_keys = [str(k).strip().lower() for k in key_param if k]
                if rec_key_str not in allowed_keys:
                    return False
            else:
                if rec_key_str != str(key_param).strip().lower():
                    return False

        return True

    def _matches_record(self, record: RawRecord, params: dict[str, Any]) -> bool:
        """Evaluate all applicable filters against a single record."""
        if not params:
            return True

        if not self._matches_time(record, params):
            return False

        if not self._matches_service(record, params):
            return False

        if self.source_type == SourceType.LOGS:
            return self._matches_logs(record, params)
        elif self.source_type == SourceType.METRICS:
            return self._matches_metrics(record, params)
        elif self.source_type == SourceType.CHANGES:
            return self._matches_changes(record, params)
        elif self.source_type == SourceType.DEPLOYMENTS:
            return self._matches_deployments(record, params)
        elif self.source_type == SourceType.PIPELINES:
            return self._matches_pipelines(record, params)
        elif self.source_type == SourceType.CONFIGURATION:
            return self._matches_configuration(record, params)

        return True

    @staticmethod
    def _extract_limit(params: dict[str, Any]) -> int | None:
        """Extract positive integer limit parameter if provided."""
        limit_val = (
            params.get("limit")
            or params.get("max_commits")
            or params.get("page_size")
            or params.get("max_results")
        )
        if limit_val is not None:
            try:
                lim = int(limit_val)
                if lim > 0:
                    return lim
            except (ValueError, TypeError):
                pass
        return None

    def query(self, query: SourceQuery) -> SourceResult:
        """Execute query against loaded fixture data and return SourceResult."""
        if not self.available or self.simulated_status == SourceStatus.UNAVAILABLE:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings or ["Source is unavailable."],
            )

        if self.simulated_status == SourceStatus.TIMEOUT:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.TIMEOUT,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings or ["Source query timed out."],
            )

        if self.simulated_status == SourceStatus.ERROR:
            return QueryResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=self.simulated_warnings
                or [self.simulated_error or "Source query error occurred."],
            )

        params: dict[str, Any] = query.parameters or {}

        # 1. Apply filtering across all loaded records
        filtered_records: list[RawRecord] = []
        for record in self._records:
            if not self._matches_record(record, params):
                continue
            filtered_records.append(record)

        # 2. Check if records were filtered out to empty
        warnings = list(self.simulated_warnings)
        if len(self._records) > 0 and len(filtered_records) == 0:
            warnings.append("No records matched the requested filters.")

        # 3. Apply limit / max_commits
        limit = self._extract_limit(params)
        truncated = False
        if limit is not None and len(filtered_records) > limit:
            filtered_records = filtered_records[:limit]
            truncated = True
            warnings.append(f"Results truncated to limit of {limit} records.")

        return QueryResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=self.simulated_status,
            truncated=truncated,
            records=filtered_records,
            warnings=warnings,
        )

