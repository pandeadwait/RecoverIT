"""Kubernetes deployment adapter for live deployment and rollout inspection.

Implements SourceAdapter to query Kubernetes Deployment resources and rollout
status via Kubernetes REST API or kubectl. Strictly read-only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

import httpx

from collectors.base import SourceAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed

logger = logging.getLogger(__name__)


class KubernetesDeploymentAdapter(SourceAdapter):
    """Real read-only source adapter for querying Kubernetes deployment status and rollouts."""

    source_type: SourceType = SourceType.DEPLOYMENTS
    adapter_name: str = "kubernetes"

    def __init__(
        self,
        api_server_url: str | None = None,
        kubeconfig_path: str | Path | None = None,
        namespace: str = "default",
        bearer_token: str | None = None,
        timeout_seconds: float = 10.0,
        verify_ssl: bool = True,
    ) -> None:
        self.api_server_url = (
            api_server_url
            or os.environ.get("K8S_API_URL")
            or os.environ.get("KUBERNETES_API_URL")
            or ""
        ).rstrip("/")
        self.kubeconfig_path = kubeconfig_path or os.environ.get("KUBECONFIG")
        self.namespace = namespace or os.environ.get("K8S_NAMESPACE") or "default"
        self.bearer_token = bearer_token or os.environ.get("K8S_TOKEN")
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl

    def is_configured(self) -> bool:
        """Check whether Kubernetes cluster connection is configured."""
        if self.api_server_url:
            return True
        if self.kubeconfig_path and Path(self.kubeconfig_path).exists():
            return True
        # Check standard default kubeconfig location
        default_kc = Path.home() / ".kube" / "config"
        if default_kc.exists():
            return True
        # In-cluster service account check
        in_cluster_sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        if in_cluster_sa.exists():
            return True
        return False

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return the capability descriptor for this Kubernetes adapter."""
        is_avail = self.is_configured()
        return SourceCapability(
            source_type=SourceType.DEPLOYMENTS,
            available=is_avail,
            supported_query_fields=[
                "service",
                "deployment",
                "namespace",
                "start_time",
                "end_time",
                "since",
                "until",
                "limit",
                "status",
            ],
            maximum_window_seconds=86400 * 30,
            maximum_items=100,
            adapter_name=self.adapter_name,
            unavailable_reason=None if is_avail else "Kubernetes cluster is not configured (set KUBECONFIG or K8S_API_URL).",
        )

    async def query(self, query: EvidenceQuery) -> SourceResult:
        """Query Kubernetes deployments asynchronously."""
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
                warnings=["Kubernetes cluster connection is not configured."],
                started_at=started_at,
                completed_at=completed_at,
            )

        # Prefer HTTP API if url configured, else kubectl fallback
        if self.api_server_url:
            return await self._query_http(query, started_at)
        return await asyncio.to_thread(self._query_kubectl, query, started_at)

    async def _query_http(self, query: EvidenceQuery, started_at: datetime) -> SourceResult:
        params = query.parameters or {}
        limit = int(params.get("limit") or 20)
        ns = params.get("namespace") or self.namespace
        target_service = params.get("service")
        deployment_name = params.get("deployment")

        url = f"{self.api_server_url}/apis/apps/v1/namespaces/{ns}/deployments"
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
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                completed_at = datetime.now(timezone.utc)
                return SourceResult(
                    query_id=query.query_id,
                    source_type=self.source_type,
                    source_adapter=self.adapter_name,
                    source_status=SourceStatus.ERROR,
                    truncated=False,
                    records=[],
                    warnings=[f"Kubernetes API HTTP {resp.status_code}: {resp.text[:200]}"],
                    started_at=started_at,
                    completed_at=completed_at,
                )

            body = resp.json()
            items = body.get("items", [])
            return self._process_deployment_items(query, items, limit, target_service, deployment_name, started_at)

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[f"Kubernetes API request failed: {type(exc).__name__}: {str(exc)}"],
                started_at=started_at,
                completed_at=completed_at,
            )
        finally:
            if close_client:
                await client.aclose()


    def _query_kubectl(self, query: EvidenceQuery, started_at: datetime) -> SourceResult:
        params = query.parameters or {}
        limit = int(params.get("limit") or 20)
        ns = params.get("namespace") or self.namespace
        target_service = params.get("service")
        deployment_name = params.get("deployment")

        cmd = ["kubectl", "get", "deployments", "-n", str(ns), "-o", "json"]
        if self.kubeconfig_path:
            cmd.extend(["--kubeconfig", str(self.kubeconfig_path)])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode != 0:
                completed_at = datetime.now(timezone.utc)
                return SourceResult(
                    query_id=query.query_id,
                    source_type=self.source_type,
                    source_adapter=self.adapter_name,
                    source_status=SourceStatus.ERROR,
                    truncated=False,
                    records=[],
                    warnings=[f"kubectl get deployments failed: {res.stderr.strip()[:200]}"],
                    started_at=started_at,
                    completed_at=completed_at,
                )

            body = json.loads(res.stdout)
            items = body.get("items", [])
            return self._process_deployment_items(query, items, limit, target_service, deployment_name, started_at)

        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            return SourceResult(
                query_id=query.query_id,
                source_type=self.source_type,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                truncated=False,
                records=[],
                warnings=[f"kubectl invocation failed: {type(exc).__name__}: {str(exc)}"],
                started_at=started_at,
                completed_at=completed_at,
            )

    def _process_deployment_items(
        self,
        query: EvidenceQuery,
        items: list[dict[str, Any]],
        limit: int,
        target_service: str | None,
        deployment_name: str | None,
        started_at: datetime,
    ) -> SourceResult:
        records: list[RawRecord] = []
        truncated = False

        params = query.parameters or {}
        start_param = params.get("start_time") or params.get("since")
        end_param = params.get("end_time") or params.get("until")
        start_dt = None
        end_dt = None
        if start_param:
            try:
                start_dt = datetime.fromisoformat(str(start_param).replace("Z", "+00:00"))
            except Exception:
                pass
        if end_param:
            try:
                end_dt = datetime.fromisoformat(str(end_param).replace("Z", "+00:00"))
            except Exception:
                pass

        for item in items:
            if truncated:
                break

            metadata = item.get("metadata", {})
            name = metadata.get("name", "unknown")

            if deployment_name and deployment_name != name:
                continue
            if target_service and target_service not in name:
                continue

            creation_str = metadata.get("creationTimestamp")
            event_time = None
            if creation_str:
                try:
                    event_time = datetime.fromisoformat(creation_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            if event_time is not None:
                if start_dt and event_time < start_dt:
                    continue
                if end_dt and event_time > end_dt:
                    continue

            spec = item.get("spec", {})
            status = item.get("status", {})
            uid = str(metadata.get("uid", name))
            rec_id = f"deployment-{name}-{uid[:8]}"

            # Extract container images
            template_spec = spec.get("template", {}).get("spec", {})
            containers = template_spec.get("containers", [])
            images = [c.get("image") for c in containers if c.get("image")]

            payload = {
                "deployment": name,
                "service": target_service or name,
                "namespace": metadata.get("namespace"),
                "replicas": spec.get("replicas"),
                "ready_replicas": status.get("readyReplicas", 0),
                "updated_replicas": status.get("updatedReplicas", 0),
                "available_replicas": status.get("availableReplicas", 0),
                "unavailable_replicas": status.get("unavailableReplicas", 0),
                "images": images,
                "conditions": status.get("conditions", []),
                "status": {
                    "ready_replicas": status.get("readyReplicas", 0),
                    "unavailable_replicas": status.get("unavailableReplicas", 0),
                    "conditions": status.get("conditions", []),
                },
                "labels": metadata.get("labels", {}),
                "creation_timestamp": creation_str,
            }

            records.append(
                RawRecord(
                    source_record_id=rec_id,
                    event_time=event_time,
                    observed_at=datetime.now(timezone.utc),
                    content_type="application/json",
                    payload=payload,
                )
            )

            if len(records) > limit:
                records.pop()
                truncated = True
                break

        completed_at = datetime.now(timezone.utc)
        stat = SourceStatus.EMPTY if len(records) == 0 else SourceStatus.OK
        warnings: list[str] = []
        if truncated:
            warnings.append(f"Result set truncated to limit of {limit} deployments.")

        return SourceResult(
            query_id=query.query_id,
            source_type=self.source_type,
            source_adapter=self.adapter_name,
            source_status=stat,
            truncated=truncated,
            records=records,
            warnings=warnings,
            started_at=started_at,
            completed_at=completed_at,
        )
