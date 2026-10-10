"""Unit and behavior tests for the 7 real read-only adapters.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §10.4:
1. LocalGitChangeAdapter (collectors/changes/local_git.py)
2. FileLogAdapter (collectors/logs/file.py)
3. PrometheusMetricAdapter (collectors/metrics/prometheus.py)
4. GitHubActionsPipelineAdapter (collectors/pipelines/github_actions.py)
5. KubernetesDeploymentAdapter (collectors/deployments/kubernetes.py)
6. GitConfigurationAdapter (collectors/configuration/git_configuration.py)
7. HttpHealthAdapter (collectors/health/http_health.py)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import pytest
import httpx

from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.health.http_health import HttpHealthAdapter
from collectors.logs.file import FileLogAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from contracts.collection.schemas import EvidenceQuery
from contracts.enums import SourceStatus, SourceType


# =============================================================================
# 1. LocalGitChangeAdapter Tests
# =============================================================================

def test_local_git_uninitialized_is_unavailable(tmp_path: Path):
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    adapter = LocalGitChangeAdapter(repo_path=empty_dir)
    assert adapter.is_git_repository() is False
    cap = adapter.get_capability()
    assert cap.available is False
    assert "not a valid Git repository" in (cap.unavailable_reason or "")


@pytest.mark.asyncio
async def test_local_git_commits_and_diff_bounding(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Developer"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True)

    # Make 3 commits
    for i in range(1, 4):
        f = repo / f"file_{i}.txt"
        f.write_text(f"line 1\nline 2\nversion {i}\n")
        subprocess.run(["git", "add", f"file_{i}.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", f"Commit {i} for service"], cwd=repo, check=True)

    adapter = LocalGitChangeAdapter(repo_path=repo, max_diff_lines=5)
    query = EvidenceQuery(
        query_id="q-git-all",
        source_type=SourceType.CHANGES,
        question="Inspect recent commits",
        parameters={"limit": 2},
    )
    result = await adapter.query(query)
    assert result.source_status == SourceStatus.OK
    assert len(result.records) == 2
    assert result.truncated is True

    record = result.records[0]
    assert "commit_hash" in record.payload
    assert "message" in record.payload
    assert "author" in record.payload
    assert "changed_files" in record.payload


# =============================================================================
# 2. FileLogAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_file_log_filtering_and_parsing(tmp_path: Path):
    log_file = tmp_path / "service.log"
    now_iso = datetime.now(timezone.utc).isoformat()
    lines = [
        f"{now_iso} INFO [auth] User login successful cid=c-123\n",
        f"{now_iso} ERROR [auth] Password authentication failed cid=c-456\n",
        f"{now_iso} ERROR [payment] Gateway timeout on charge cid=c-789\n",
        json.dumps({
            "timestamp": now_iso,
            "level": "ERROR",
            "service": "payment",
            "message": "Stripe API 500 error",
            "correlation_id": "c-999",
        }) + "\n",
    ]
    log_file.write_text("".join(lines))

    adapter = FileLogAdapter(log_path=log_file)

    # Filter by service = payment
    q_service = EvidenceQuery(
        query_id="q-pay",
        source_type=SourceType.LOGS,
        question="Fetch payment service logs",
        parameters={"service": "payment"},
    )
    res_service = await adapter.query(q_service)
    assert res_service.source_status == SourceStatus.OK
    assert len(res_service.records) == 2
    assert all("payment" in r.payload.get("service", "") or "payment" in r.payload.get("raw", "") for r in res_service.records)

    # Filter by severity = ERROR
    q_err = EvidenceQuery(
        query_id="q-err",
        source_type=SourceType.LOGS,
        question="Fetch error level logs",
        parameters={"severity": "ERROR"},
    )
    res_err = await adapter.query(q_err)
    assert len(res_err.records) == 3

    # Filter by pattern
    q_pat = EvidenceQuery(
        query_id="q-pat",
        source_type=SourceType.LOGS,
        question="Find gateway timeout pattern",
        parameters={"pattern": "Gateway timeout"},
    )
    res_pat = await adapter.query(q_pat)
    assert len(res_pat.records) == 1
    assert "Gateway timeout" in res_pat.records[0].payload.get("raw", "")


@pytest.mark.asyncio
async def test_file_log_normalizes_naive_standard_timestamp_to_utc(tmp_path: Path):
    """Standard Python logs must not violate RawRecord's aware-time contract."""
    log_file = tmp_path / "service.log"
    log_file.write_text(
        "2026-10-10 15:21:56,485 ERROR [payment-api] checkout failed\n"
    )

    adapter = FileLogAdapter(log_path=log_file, only_errors=True)
    result = await adapter.query(
        EvidenceQuery(
            query_id="q-naive-log-time",
            source_type=SourceType.LOGS,
            question="Fetch payment failures",
            parameters={},
        )
    )

    assert result.source_status == SourceStatus.OK
    assert len(result.records) == 1
    assert result.records[0].event_time == datetime(
        2026, 10, 10, 15, 21, 56, 485000, tzinfo=timezone.utc
    )


# =============================================================================
# 3. PrometheusMetricAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_prometheus_matrix_and_vector_query():
    now_ts = datetime.now(timezone.utc).timestamp()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/query_range" in request.url.path:
            return httpx.Response(200, json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_requests_total", "code": "500"},
                            "values": [[now_ts - 120, "2"], [now_ts - 60, "8"], [now_ts, "15"]],
                        }
                    ],
                },
            })
        elif "/query" in request.url.path:
            return httpx.Response(200, json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"__name__": "up", "instance": "server-1"},
                            "value": [now_ts, "1"],
                        }
                    ],
                },
            })
        return httpx.Response(400)

    adapter = PrometheusMetricAdapter(endpoint_url="http://prom.mock:9090")
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom.mock:9090")

    # Range query
    q_range = EvidenceQuery(
        query_id="q-range",
        source_type=SourceType.METRICS,
        question="Query Prometheus range",
        parameters={
            "metric": "http_requests_total",
            "start_time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
        },
    )
    res_range = await adapter.query(q_range)
    assert res_range.source_status == SourceStatus.OK
    assert len(res_range.records) == 3
    assert res_range.records[0].payload.get("value") == 2.0


    # Instant vector query
    q_instant = EvidenceQuery(
        query_id="q-instant",
        source_type=SourceType.METRICS,
        question="Query instant metric",
        parameters={"metric": "up"},
    )
    res_instant = await adapter.query(q_instant)
    assert res_instant.source_status == SourceStatus.OK
    assert res_instant.records[0].payload.get("value") == 1.0



# =============================================================================
# 4. GitHubActionsPipelineAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_github_actions_workflow_runs_and_failure_details():
    now_iso = datetime.now(timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer fake_token_abc"
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 999,
                        "name": "Integration Tests",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_branch": "feature/payments",
                        "head_sha": "f123456",
                        "html_url": "https://github.com/org/repo/actions/runs/999",
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    }
                ],
            })
        elif "/runs/999/jobs" in request.url.path:
            return httpx.Response(200, json={
                "jobs": [
                    {
                        "id": 888,
                        "name": "test-backend",
                        "conclusion": "failure",
                        "steps": [
                            {"name": "Setup", "conclusion": "success"},
                            {"name": "Pytest", "conclusion": "failure"},
                        ],
                    }
                ]
            })
        return httpx.Response(404)

    adapter = GitHubActionsPipelineAdapter(
        repository="org/repo",
        github_token="fake_token_abc",
    )
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")

    query = EvidenceQuery(
        query_id="q-gh",
        source_type=SourceType.PIPELINES,
        question="Fetch completed workflow runs",
        parameters={"status": "completed"},
    )
    result = await adapter.query(query)
    assert result.source_status == SourceStatus.OK
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.payload.get("conclusion") == "failure"
    assert "jobs" in rec.payload
    assert rec.payload["jobs"][0]["failed_steps"] == ["Pytest"]


# =============================================================================
# 5. KubernetesDeploymentAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_kubernetes_deployment_rollout_observations():
    now_iso = datetime.now(timezone.utc).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer k8s_secret_tok"
        return httpx.Response(200, json={
            "items": [
                {
                    "metadata": {
                        "name": "order-api",
                        "namespace": "production",
                        "creationTimestamp": now_iso,
                    },
                    "spec": {
                        "replicas": 5,
                        "template": {
                            "spec": {
                                "containers": [{"name": "app", "image": "myreg.io/order-api:1.4.2"}]
                            }
                        },
                    },
                    "status": {
                        "replicas": 5,
                        "readyReplicas": 2,
                        "availableReplicas": 2,
                        "unavailableReplicas": 3,
                        "conditions": [
                            {
                                "type": "Progressing",
                                "status": "False",
                                "reason": "ProgressDeadlineExceeded",
                                "message": "ReplicaSet did not finish progress.",
                            }
                        ],
                    },
                }
            ]
        })

    adapter = KubernetesDeploymentAdapter(
        api_server_url="https://k8s.mock:6443",
        bearer_token="k8s_secret_tok",
        namespace="production",
    )
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://k8s.mock:6443")

    query = EvidenceQuery(
        query_id="q-k8s",
        source_type=SourceType.DEPLOYMENTS,
        question="Observe order-api deployment status",
        parameters={"service": "order-api"},
    )
    result = await adapter.query(query)
    assert result.source_status == SourceStatus.OK
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.payload["service"] == "order-api"
    assert rec.payload["images"] == ["myreg.io/order-api:1.4.2"]
    assert rec.payload["status"]["unavailable_replicas"] == 3
    assert rec.payload["status"]["conditions"][0]["reason"] == "ProgressDeadlineExceeded"


# =============================================================================
# 6. GitConfigurationAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_git_configuration_diff_and_redaction(tmp_path: Path):
    repo = tmp_path / "conf_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ConfigManager"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "cm@example.com"], cwd=repo, check=True)

    config_dir = repo / "config"
    config_dir.mkdir()
    f = config_dir / "database.env"
    f.write_text("DB_HOST=localhost\nDB_PORT=5432\n")
    subprocess.run(["git", "add", "config/database.env"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial env config"], cwd=repo, check=True)

    f.write_text("DB_HOST=db.prod.internal\nDB_PORT=5432\nDB_PASSWORD=my_super_secure_password_999\n")
    subprocess.run(["git", "add", "config/database.env"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Change host and set password"], cwd=repo, check=True)

    adapter = GitConfigurationAdapter(repo_path=repo)
    query = EvidenceQuery(
        query_id="q-conf",
        source_type=SourceType.CONFIGURATION,
        question="Fetch configuration diffs",
        parameters={"path": "config/"},
    )
    result = await adapter.query(query)
    assert result.source_status == SourceStatus.OK
    assert len(result.records) == 2

    latest_rec = result.records[0]
    diff_text = latest_rec.payload.get("diff", "")
    assert "my_super_secure_password_999" not in diff_text
    assert "[REDACTED]" in diff_text


# =============================================================================
# 7. HttpHealthAdapter Tests
# =============================================================================

@pytest.mark.asyncio
async def test_http_health_latency_and_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if "failing" in str(request.url):
            return httpx.Response(503, json={"status": "DOWN", "error": "Database disconnected"})
        return httpx.Response(200, json={"status": "UP", "version": "1.0.0"})

    adapter = HttpHealthAdapter(
        endpoints={
            "orders": "http://health.mock:8080/health/orders",
            "failing-svc": "http://health.mock:8080/health/failing",
        }
    )
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # Query healthy service
    q_healthy = EvidenceQuery(
        query_id="q-healthy",
        source_type=SourceType.HEALTH,
        question="Check health of orders endpoint",
        parameters={"service": "orders"},
    )
    res_healthy = await adapter.query(q_healthy)
    assert res_healthy.source_status == SourceStatus.OK
    assert len(res_healthy.records) == 1
    assert res_healthy.records[0].payload["status_code"] == 200
    assert res_healthy.records[0].payload["healthy"] is True
    assert "latency_ms" in res_healthy.records[0].payload

    # Query failing service
    q_failing = EvidenceQuery(
        query_id="q-failing",
        source_type=SourceType.HEALTH,
        question="Check health of failing endpoint",
        parameters={"service": "failing-svc"},
    )
    res_failing = await adapter.query(q_failing)
    assert res_failing.source_status == SourceStatus.OK
    assert len(res_failing.records) == 1
    assert res_failing.records[0].payload["status_code"] == 503
    assert res_failing.records[0].payload["healthy"] is False
