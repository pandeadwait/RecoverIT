"""Prometheus metric adapter for live timeseries metric inspection.

Implements SourceAdapter to query Prometheus HTTP API endpoints (query and query_range).
Strictly read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any
import urllib.parse

import httpx

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)


class PrometheusMetricAdapter(SourceAdapter):
    """Real read-only source adapter for querying metrics from Prometheus HTTP API."""

    source_type: SourceType = SourceType.METRICS
    adapter_name: str = "prometheus"

    def __init__(
        self,
        endpoint_url: str | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = 10.0,
        verify_ssl: bool = True,
    ) -> None:
        self.endpoint_url = (endpoint_url or os.environ.get("PROMETHEUS_URL") or "").rstrip("/")
        self.bearer_token = bearer_token or os.environ.get("PROMETHEUS_TOKEN")
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl

    def is_configured(self) -> bool:
        """Check whether a Prometheus endpoint is configured."""
        return bool(self.endpoint_url)

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this Prometheus adapter."""
        is_avail = self.is_configured()
        return SourceCapability(
            source_type=SourceType.METRICS,
            available=is_avail,
            supported_query_fields=[
                "metric_name",
                "metric",
                "query",
                "start_time",
                "end_time",
                "since",
                "until",
                "step",
                "step_seconds",
                "limit",
                "service",
                "labels",
            ],
            maximum_window_seconds=86400 * 7,
            maximum_items=1000,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else "Prometheus endpoint URL is not configured (set PROMETHEUS_URL).",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Query Prometheus asynchronously via HTTP API."""
        started_at = datetime.now(timezone.utc)
        if not self.is_configured():
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.UNAVAILABLE,
                truncated=False,
                records=[],
                warnings=["Prometheus endpoint is not configured."],
                started_at=started_at,
                completed_at=completed_at,
            )

        params = query.parameters or {}
        limit = int(params.get("limit") or 200)

        # 1. Build PromQL query
        promql = params.get("query")
        if not promql:
            metric_name = params.get("metric_name") or params.get("metric") or "up"
            service = params.get("service")
            labels = params.get("labels") or {}
            label_parts: list[str] = []
            if service:
                label_parts.append(f'service="{service}"')
            if isinstance(labels, dict):
                for k, v in labels.items():
                    label_parts.append(f'{k}="{v}"')

            if label_parts:
                promql = f"{metric_name}{{{', '.join(label_parts)}}}"
            else:
                promql = str(metric_name)

        start_time = params.get("start_time") or params.get("since")
        end_time = params.get("end_time") or params.get("until")
        step = params.get("step") or params.get("step_seconds") or "15s"

        start_dt: datetime | None = None
        end_dt: datetime | None = None
        if start_time:
            try:
                start_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
            except Exception:
                try:
                    start_dt = datetime.fromtimestamp(float(start_time), tz=timezone.utc)
                except Exception:
                    pass
        if end_dt:
            try:
                end_dt = datetime.fromisoformat(str(end_param if (end_param := end_time) else "").replace("Z", "+00:00"))
            except Exception:
                try:
                    end_dt = datetime.fromtimestamp(float(end_time), tz=timezone.utc)
                except Exception:
                    pass

        headers: dict[str, str] = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"

        client = getattr(self, "_client", None)
        close_client = False
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                verify=self.verify_ssl,
            )
            close_client = True

        try:
            if True:

                if start_time and end_time:
                    # Range query
                    url = f"{self.endpoint_url}/api/v1/query_range"
                    req_params = {
                        "query": promql,
                        "start": str(start_time),
                        "end": str(end_time),
                        "step": str(step),
                    }
                else:
                    # Instant query
                    url = f"{self.endpoint_url}/api/v1/query"
                    req_params = {"query": promql}
                    if end_time:
                        req_params["time"] = str(end_time)

                resp = await client.get(url, params=req_params, headers=headers)
                if resp.status_code != 200:
                    completed_at = datetime.now(timezone.utc)
                    return SourceResult(
                        query_id=query.query_id,
                        source_type=self.source_type,
                        source_adapter=self.adapter_name,
                        source_status=SourceStatus.ERROR,
                        truncated=False,
                        records=[],
                        warnings=[f"Prometheus HTTP error {resp.status_code}: {resp.text[:200]}"],
                        started_at=started_at,
                        completed_at=completed_at,
                    )

                body = resp.json()
                if body.get("status") != "success":
                    completed_at = datetime.now(timezone.utc)
                    return SourceResult(
                        query_id=query.query_id,
                        source_type=self.source_type,
                        source_adapter=self.adapter_name,
                        source_status=SourceStatus.ERROR,
                        truncated=False,
                        records=[],
                        warnings=[f"Prometheus API error: {body.get('error', 'unknown error')}"],
                        started_at=started_at,
                        completed_at=completed_at,
                    )

                data = body.get("data", {})
                result_type = data.get("resultType")
                result_items = data.get("result", [])

                records: list[RawRecord] = []
                truncated = False

                for series in result_items:
                    if truncated:
                        break
                    metric_labels = series.get("metric", {})
                    m_name = metric_labels.get("__name__", "custom_metric")

                    if result_type == "matrix":
                        values = series.get("values", [])
                        for ts_val, sample_val in values:
                            try:
                                dt = datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
                            except Exception:
                                dt = datetime.now(timezone.utc)

                            if start_dt and dt < start_dt:
                                continue
                            if end_dt and dt > end_dt:
                                continue

                            rec_id = f"metric-{m_name}-{ts_val}"
                            records.append(
                                RawRecord(
                                    source_record_id=rec_id,
                                    event_time=dt,
                                    observed_at=datetime.now(timezone.utc),
                                    content_type="application/json",
                                    payload={
                                        "metric_name": m_name,
                                        "labels": metric_labels,
                                        "value": float(sample_val) if sample_val not in ("NaN", "Inf", "-Inf") else str(sample_val),
                                        "timestamp": ts_val,
                                        "promql": promql,
                                    },
                                )
                            )
                            if len(records) > limit:
                                records.pop()
                                truncated = True
                                break
                    elif result_type == "vector":
                        val_tuple = series.get("value", [])
                        if len(val_tuple) >= 2:
                            ts_val, sample_val = val_tuple[0], val_tuple[1]
                            try:
                                dt = datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
                            except Exception:
                                dt = datetime.now(timezone.utc)

                            if start_dt and dt < start_dt:
                                continue
                            if end_dt and dt > end_dt:
                                continue

                            rec_id = f"metric-{m_name}-{ts_val}"
                            records.append(
                                RawRecord(
                                    source_record_id=rec_id,
                                    event_time=dt,
                                    observed_at=datetime.now(timezone.utc),
                                    content_type="application/json",
                                    payload={
                                        "metric_name": m_name,
                                        "labels": metric_labels,
                                        "value": float(sample_val) if sample_val not in ("NaN", "Inf", "-Inf") else str(sample_val),
                                        "timestamp": ts_val,
                                        "promql": promql,
                                    },
                                )
                            )
                            if len(records) > limit:
                                records.pop()
                                truncated = True
                                break

                completed_at = datetime.now(timezone.utc)
                status = SourceStatus.EMPTY if len(records) == 0 else SourceStatus.OK
                warnings: list[str] = []
                if truncated:
                    warnings.append(f"Result set truncated to limit of {limit} metric points.")

                return SourceResult(
                    query_id=query.query_id,
                    source_type=self.source_type,
                    source_adapter=self.adapter_name,
                    source_status=status,
                    truncated=truncated,
                    records=records,
                    warnings=warnings,
                    started_at=started_at,
                    completed_at=completed_at,
                )

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            logger.warning("Prometheus query failed: %s", exc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[f"Prometheus request failed: {type(exc).__name__}: {str(exc)}"],
                started_at=started_at,
                completed_at=completed_at,
            )
        finally:
            if close_client:
                await client.aclose()

