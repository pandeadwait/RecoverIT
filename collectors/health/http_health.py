"""HTTP health adapter for live endpoint health and availability inspection.

Implements SourceAdapter to query operational HTTP health check endpoints (/health, /healthz),
measuring latency and verifying service availability. Strictly read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any

import httpx

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)


class HttpHealthAdapter(SourceAdapter):
    """Real read-only source adapter for querying service HTTP health endpoints."""

    source_type: SourceType = SourceType.HEALTH
    adapter_name: str = "http_health"

    def __init__(
        self,
        endpoints: dict[str, str] | None = None,
        default_url: str | None = None,
        timeout_seconds: float = 5.0,
        verify_ssl: bool = True,
    ) -> None:
        self.endpoints = dict(endpoints or {})
        self.default_url = default_url or os.environ.get("HEALTH_ENDPOINT_URL") or os.environ.get("SERVICE_HEALTH_URL")
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl

    def is_configured(self) -> bool:
        """Check whether any health endpoint URL is configured."""
        return bool(self.endpoints or self.default_url)

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this HTTP health adapter."""
        is_avail = self.is_configured()
        return SourceCapability(
            source_type=SourceType.HEALTH,
            available=is_avail,
            supported_query_fields=[
                "service",
                "component",
                "endpoint",
                "url",
                "start_time",
                "end_time",
                "limit",
            ],
            maximum_window_seconds=86400,
            maximum_items=100,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else "No HTTP health endpoints configured (set HEALTH_ENDPOINT_URL).",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Execute HTTP health check query asynchronously."""
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
                warnings=["HTTP health check endpoints are not configured."],
                started_at=started_at,
                completed_at=completed_at,
            )

        params = query.parameters or {}
        limit = int(params.get("limit") or 10)
        target_service = params.get("service") or "application"

        # Determine target endpoint URL
        target_url = (
            params.get("url")
            or params.get("endpoint")
            or self.endpoints.get(target_service)
            or self.default_url
        )

        if not target_url:
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.EMPTY,
                truncated=False,
                records=[],
                warnings=[f"No health endpoint configured for service '{target_service}'."],
                started_at=started_at,
                completed_at=completed_at,
            )

        headers = {"Accept": "application/json, text/plain, */*"}
        records: list[RawRecord] = []
        warnings: list[str] = []

        client = getattr(self, "_client", None)
        close_client = False
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                verify=self.verify_ssl,
            )
            close_client = True

        try:
            start_tick = time.monotonic()
            resp = await client.get(target_url, headers=headers)
            latency_ms = (time.monotonic() - start_tick) * 1000.0

            now_utc = datetime.now(timezone.utc)
            is_healthy = 200 <= resp.status_code < 300
            health_status = "UP" if is_healthy else "DOWN"

            # Parse response body
            body_content: Any
            try:
                body_content = resp.json()
            except Exception:
                body_content = resp.text[:500]

            rec_id = f"health-{target_service}-{int(now_utc.timestamp())}"
            payload = {
                "service": target_service,
                "url": target_url,
                "status_code": resp.status_code,
                "latency_ms": round(latency_ms, 2),
                "health_status": health_status,
                "healthy": is_healthy,
                "response": body_content,
                "checked_at": now_utc.isoformat(),
            }

            records.append(
                RawRecord(
                    source_record_id=rec_id,
                    event_time=now_utc,
                    observed_at=now_utc,
                    content_type="application/json",
                    payload=payload,
                )
            )

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            logger.warning("HTTP health check failed for %s: %s", target_url, exc)
            rec_id = f"health-{target_service}-{int(started_at.timestamp())}"
            # Record explicit unreachable observation
            records.append(
                RawRecord(
                    source_record_id=rec_id,
                    event_time=started_at,
                    observed_at=datetime.now(timezone.utc),
                    content_type="application/json",
                    payload={
                        "service": target_service,
                        "url": target_url,
                        "health_status": "DOWN",
                        "healthy": False,
                        "status_code": 0,
                        "error": f"{type(exc).__name__}: {str(exc)}",
                        "checked_at": started_at.isoformat(),
                    },
                )
            )
            warnings.append(f"HTTP health check failed: {type(exc).__name__}: {str(exc)}")
        finally:
            if close_client:
                await client.aclose()


        completed_at = datetime.now(timezone.utc)
        status = SourceStatus.OK if records else SourceStatus.EMPTY

        return SourceResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=status,
            truncated=False,
            records=records[:limit],
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
        )
