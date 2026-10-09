"""Shared adapter contract test suite.

Per LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md §15.4:
Every adapter must pass tests for:
1. Correct source_type and stable adapter_name.
2. Accurate capability availability.
3. Supported query fields.
4. Start/end time filtering.
5. Service filtering where supported.
6. Result limit and truncation.
7. Empty successful result.
8. Unavailable source.
9. Timeout behavior.
10. Malformed source response.
11. Sanitized errors.
12. Stable provenance identifiers.
13. No source mutation.
14. No fixed scenario values in returned records.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from typing import Any
import pytest
import httpx

from collectors.base import SourceAdapter
from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.health.http_health import HttpHealthAdapter
from collectors.logs.file import FileLogAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from contracts.collection.schemas import EvidenceQuery, RawRecord, SourceCapability, SourceResult
from contracts.common import Severity
from contracts.enums import SourceStatus, SourceType
from contracts.incident.schemas import IncidentSeed
from tests.support.scripted_source import ScriptedSourceAdapter


@pytest.fixture
def sample_incident() -> IncidentSeed:
    now = datetime.now(timezone.utc)
    return IncidentSeed(
        incident_id="inc-test-contract-001",
        external_alert_id="alt-contract-001",
        service="checkout-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=now - timedelta(minutes=25),
        received_at=now,
        summary="Test Incident for Adapter Contracts",
    )


# -----------------------------------------------------------------------------
# Factory helpers
# -----------------------------------------------------------------------------

def build_available_adapter(name: str, tmp_path: Path) -> SourceAdapter:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    if name == "git":
        repo = tmp_path / "git_repo"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

        file1 = repo / "main.py"
        file1.write_text("print('version 1')\n")
        subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

        file1.write_text("print('version 2 - fix')\n")
        subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Fix checkout service logic"], cwd=repo, check=True)
        return LocalGitChangeAdapter(repo_path=repo, service_name="checkout-service")

    elif name == "log":
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"
        earlier_iso = (now - timedelta(hours=2)).isoformat()
        lines = [
            f'{earlier_iso} INFO [checkout-service] Server started successfully\n',
            f'{now_iso} ERROR [checkout-service] Database connection timeout in payment processor\n',
            f'{now_iso} WARN [other-service] Retrying failed request\n',
            json.dumps({
                "timestamp": now_iso,
                "level": "ERROR",
                "service": "checkout-service",
                "message": "Out of memory error in pool",
            }) + "\n",
        ]
        log_file.write_text("".join(lines))
        return FileLogAdapter(log_path=log_file, service_name="checkout-service")

    elif name == "prometheus":
        now_ts = now.timestamp()

        def prom_handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            query_str = params.get("query", "")
            if "non-existent" in query_str or "DEFINITELY_NO_MATCH" in query_str:
                return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})

            data = {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_requests_failed_total", "service": "checkout-service"},
                            "values": [[now_ts - 60, "5"], [now_ts, "12"]],
                        }
                    ],
                },
            }
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(prom_handler)
        adapter = PrometheusMetricAdapter(endpoint_url="http://prometheus.mock:9090")
        adapter._client = httpx.AsyncClient(transport=transport, base_url="http://prometheus.mock:9090")
        return adapter

    elif name == "github":
        def gh_handler(request: httpx.Request) -> httpx.Response:
            if "/runs" in request.url.path and not request.url.path.endswith("/jobs"):
                data = {
                    "total_count": 2,
                    "workflow_runs": [
                        {
                            "id": 101,
                            "name": "checkout-service-ci",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/org/repo/actions/runs/101",
                            "created_at": now_iso,
                            "updated_at": now_iso,
                            "head_branch": "main",
                            "head_sha": "abc1234",
                        },
                        {
                            "id": 102,
                            "name": "Deploy",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/org/repo/actions/runs/102",
                            "created_at": now_iso,
                            "updated_at": now_iso,
                            "head_branch": "main",
                            "head_sha": "def5678",
                        },
                    ],
                }
                return httpx.Response(200, json=data)
            elif "/jobs" in request.url.path:
                return httpx.Response(200, json={"jobs": [{"id": 1, "name": "build", "conclusion": "failure"}]})
            return httpx.Response(404)

        transport = httpx.MockTransport(gh_handler)
        adapter = GitHubActionsPipelineAdapter(repository="org/repo", github_token="fake_token_ghp_12345678901234567890")
        adapter._client = httpx.AsyncClient(transport=transport, base_url="https://api.github.com")
        return adapter

    elif name == "k8s":
        def k8s_handler(request: httpx.Request) -> httpx.Response:
            data = {
                "kind": "DeploymentList",
                "items": [
                    {
                        "metadata": {
                            "name": "checkout-service",
                            "namespace": "default",
                            "generation": 3,
                            "creationTimestamp": now_iso,
                        },
                        "spec": {
                            "replicas": 3,
                            "template": {
                                "spec": {
                                    "containers": [
                                        {"name": "checkout", "image": "registry/checkout:v2.1.0"}
                                    ]
                                }
                            },
                        },
                        "status": {
                            "replicas": 3,
                            "updatedReplicas": 3,
                            "readyReplicas": 1,
                            "availableReplicas": 1,
                            "unavailableReplicas": 2,
                            "conditions": [
                                {
                                    "type": "Available",
                                    "status": "False",
                                    "reason": "MinimumReplicasUnavailable",
                                    "message": "Deployment does not have minimum availability.",
                                }
                            ],
                        },
                    }
                ],
            }
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(k8s_handler)
        adapter = KubernetesDeploymentAdapter(api_server_url="https://k8s.mock:6443", bearer_token="fake_k8s_token")
        adapter._client = httpx.AsyncClient(transport=transport, base_url="https://k8s.mock:6443")
        return adapter

    elif name == "config":
        repo = tmp_path / "config_repo"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

        conf_dir = repo / "config"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_file = conf_dir / "app.yaml"
        conf_file.write_text("database:\n  max_pool_size: 10\n  timeout: 5\n")
        subprocess.run(["git", "add", "config/app.yaml"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial database config"], cwd=repo, check=True)

        conf_file.write_text("database:\n  max_pool_size: 2\n  timeout: 1\n  password: supersecretpassword123\n")
        subprocess.run(["git", "add", "config/app.yaml"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Lower pool size and update credentials"], cwd=repo, check=True)
        return GitConfigurationAdapter(repo_path=repo)

    elif name == "health":
        def health_handler(request: httpx.Request) -> httpx.Response:
            data = {
                "status": "UP",
                "service": "checkout-service",
                "checks": [{"name": "db", "status": "UP"}],
            }
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(health_handler)
        adapter = HttpHealthAdapter(endpoints={"checkout-service": "http://health.mock:8080/health"})
        adapter._client = httpx.AsyncClient(transport=transport)
        return adapter

    elif name == "scripted":
        records = [
            RawRecord(
                source_record_id="rec-001",
                content_type="application/json",
                event_time=now - timedelta(minutes=5),
                observed_at=now,
                payload={"status": "degraded", "latency_ms": 350, "service": "checkout-service"},
            ),
            RawRecord(
                source_record_id="rec-002",
                content_type="application/json",
                event_time=now,
                observed_at=now,
                payload={"status": "down", "latency_ms": 5000, "service": "checkout-service"},
            ),
        ]
        return ScriptedSourceAdapter(
            source_type=SourceType.HEALTH,
            adapter_name="scripted_source",
            available=True,
            canned_records=records,
        )

    raise ValueError(f"Unknown adapter name: {name}")


def build_unavailable_adapter(name: str, tmp_path: Path) -> SourceAdapter:
    non_existent = tmp_path / "does_not_exist_xyz"
    if name == "git":
        return LocalGitChangeAdapter(repo_path=non_existent)
    elif name == "log":
        return FileLogAdapter(log_path=non_existent / "file.log")
    elif name == "prometheus":
        return PrometheusMetricAdapter(endpoint_url="")
    elif name == "github":
        return GitHubActionsPipelineAdapter(repository="")
    elif name == "k8s":
        return KubernetesDeploymentAdapter(api_server_url="", kubeconfig_path=non_existent / "k")
    elif name == "config":
        return GitConfigurationAdapter(repo_path=non_existent)
    elif name == "health":
        return HttpHealthAdapter(endpoints={}, default_url="")
    elif name == "scripted":
        return ScriptedSourceAdapter(available=False, unavailable_reason="Disabled in test")
    raise ValueError(f"Unknown adapter name: {name}")


ADAPTER_NAMES = [
    "git",
    "log",
    "prometheus",
    "github",
    "k8s",
    "config",
    "health",
    "scripted",
]


@pytest.fixture(params=ADAPTER_NAMES)
def available_adapter(request: pytest.FixtureRequest, tmp_path: Path) -> SourceAdapter:
    return build_available_adapter(request.param, tmp_path)


@pytest.fixture(params=ADAPTER_NAMES)
def unavailable_adapter(request: pytest.FixtureRequest, tmp_path: Path) -> SourceAdapter:
    return build_unavailable_adapter(request.param, tmp_path)


# =============================================================================
# 14 Adapter Contract Tests (§15.4)
# =============================================================================

def test_1_adapter_identity(available_adapter: SourceAdapter):
    """1. Correct source_type and stable adapter_name."""
    assert isinstance(available_adapter, SourceAdapter)
    assert available_adapter.source_type in SourceType
    assert isinstance(available_adapter.adapter_name, str)
    assert len(available_adapter.adapter_name) > 0


def test_2_available_capability(available_adapter: SourceAdapter, sample_incident: IncidentSeed):
    """2a. Accurate capability when available."""
    cap = available_adapter.get_capability(sample_incident)
    assert isinstance(cap, SourceCapability)
    assert cap.available is True
    assert cap.adapter_name == available_adapter.adapter_name
    assert cap.source_type == available_adapter.source_type


def test_2_unavailable_capability(unavailable_adapter: SourceAdapter, sample_incident: IncidentSeed):
    """2b. Accurate capability when unavailable."""
    cap = unavailable_adapter.get_capability(sample_incident)
    assert isinstance(cap, SourceCapability)
    assert cap.available is False
    assert cap.adapter_name == unavailable_adapter.adapter_name
    assert cap.source_type == unavailable_adapter.source_type
    assert cap.unavailable_reason is not None
    assert len(cap.unavailable_reason) > 0


def test_3_supported_query_fields(available_adapter: SourceAdapter, sample_incident: IncidentSeed):
    """3. Supported query fields are advertised and non-empty."""
    cap = available_adapter.get_capability(sample_incident)
    assert isinstance(cap.supported_query_fields, list)
    assert len(cap.supported_query_fields) > 0
    for field in cap.supported_query_fields:
        assert isinstance(field, str)


@pytest.mark.asyncio
async def test_4_start_end_time_filtering(available_adapter: SourceAdapter):
    """4. Start/end time filtering excludes out-of-window data."""
    now = datetime.now(timezone.utc)
    future_query = EvidenceQuery(
        query_id="q-future",
        source_type=available_adapter.source_type,
        question="Inspect future window",
        parameters={
            "start_time": (now + timedelta(days=10)).isoformat(),
            "end_time": (now + timedelta(days=11)).isoformat(),
            "since": (now + timedelta(days=10)).isoformat(),
            "until": (now + timedelta(days=11)).isoformat(),
        },
    )
    result = await available_adapter.query(future_query)
    assert isinstance(result, SourceResult)
    assert len(result.records) == 0


@pytest.mark.asyncio
async def test_5_service_filtering(available_adapter: SourceAdapter):
    """5. Service filtering where supported returns records or empty safely."""
    query = EvidenceQuery(
        query_id="q-svc-filter",
        source_type=available_adapter.source_type,
        question="Filter by checkout-service",
        parameters={"service": "checkout-service", "limit": 10},
    )
    result = await available_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert result.source_status in (SourceStatus.OK, SourceStatus.EMPTY)


@pytest.mark.asyncio
async def test_6_result_limit_and_truncation(available_adapter: SourceAdapter):
    """6. Result limit and truncation enforcement."""
    query = EvidenceQuery(
        query_id="q-limit-1",
        source_type=available_adapter.source_type,
        question="Limit results to 1 item",
        parameters={"limit": 1},
    )
    result = await available_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert len(result.records) <= 1


@pytest.mark.asyncio
async def test_7_empty_successful_result(available_adapter: SourceAdapter):
    """7. Empty successful result does not raise an exception."""
    query = EvidenceQuery(
        query_id="q-empty-match",
        source_type=available_adapter.source_type,
        question="Query matching nothing",
        parameters={
            "service": "non-existent-service-xyz",
            "pattern": "DEFINITELY_NO_MATCH_PATTERN_ZZZZZ",
            "path": "non_existent_path_xyz",
            "limit": 10,
        },
    )
    result = await available_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert result.source_status in (SourceStatus.OK, SourceStatus.EMPTY)
    assert len(result.records) == 0


@pytest.mark.asyncio
async def test_8_unavailable_source(unavailable_adapter: SourceAdapter):
    """8. Querying unavailable source returns UNAVAILABLE status, not crash."""
    query = EvidenceQuery(
        query_id="q-unavail",
        source_type=unavailable_adapter.source_type,
        question="Query unavailable adapter",
        parameters={"limit": 5},
    )
    result = await unavailable_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert result.source_status == SourceStatus.UNAVAILABLE
    assert len(result.records) == 0
    assert result.source_adapter == unavailable_adapter.adapter_name


@pytest.mark.asyncio
async def test_9_timeout_behavior():
    """9. Timeout behavior is handled cleanly."""
    scripted_slow = ScriptedSourceAdapter(
        source_type=SourceType.HEALTH,
        delay_seconds=0.2,
    )
    query = EvidenceQuery(
        query_id="q-timeout",
        source_type=SourceType.HEALTH,
        question="Slow query testing timeout",
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(scripted_slow.query(query), timeout=0.05)


@pytest.mark.asyncio
async def test_10_malformed_source_response(tmp_path: Path):
    """10. Malformed source responses are handled gracefully."""
    bad_log = tmp_path / "bad.log"
    bad_log.write_text("{this is not valid json\n{another: broken}\n")
    log_adapter = FileLogAdapter(log_path=bad_log)
    query = EvidenceQuery(
        query_id="q-bad-log",
        source_type=SourceType.LOGS,
        question="Parse bad json lines",
    )
    result = await log_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert result.source_status in (SourceStatus.OK, SourceStatus.EMPTY)

    def bad_prom_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Bad Gateway</body></html>")

    prom_adapter = PrometheusMetricAdapter(endpoint_url="http://prom.mock")
    prom_adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(bad_prom_handler))
    prom_query = EvidenceQuery(
        query_id="q-bad-prom",
        source_type=SourceType.METRICS,
        question="Parse html error page as prometheus json",
        parameters={"metric": "cpu"},
    )
    prom_res = await prom_adapter.query(prom_query)
    assert isinstance(prom_res, SourceResult)
    assert prom_res.source_status == SourceStatus.ERROR


@pytest.mark.asyncio
async def test_11_sanitized_errors(tmp_path: Path):
    """11. Sensitive secrets are redacted from payloads, warnings, and diffs."""
    config_adapter = build_available_adapter("config", tmp_path)
    query = EvidenceQuery(
        query_id="q-secret-check",
        source_type=SourceType.CONFIGURATION,
        question="Inspect secret redaction in configuration diffs",
        parameters={"limit": 10},
    )
    result = await config_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert any("[REDACTED]" in json.dumps(record.payload) for record in result.records)
    for record in result.records:
        payload_str = json.dumps(record.payload)
        assert "supersecretpassword123" not in payload_str


@pytest.mark.asyncio
async def test_12_stable_provenance_identifiers(available_adapter: SourceAdapter):
    """12. Stable provenance identifiers on all returned records."""
    query = EvidenceQuery(
        query_id="q-provenance",
        source_type=available_adapter.source_type,
        question="Inspect record provenance",
        parameters={"limit": 10},
    )
    result = await available_adapter.query(query)
    assert isinstance(result, SourceResult)
    assert result.query_id == "q-provenance"
    assert result.source_type == available_adapter.source_type
    assert result.source_adapter == available_adapter.adapter_name

    for record in result.records:
        assert isinstance(record.source_record_id, str)
        assert len(record.source_record_id) > 0
        assert isinstance(record.content_type, str)
        assert isinstance(record.payload, dict)


def test_13_no_source_mutation(tmp_path: Path):
    """13. Adapters remain strictly read-only and do not mutate sources."""
    git_adapter = build_available_adapter("git", tmp_path)
    res_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=git_adapter.repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    count_before = int(res_before.stdout.strip())

    query = EvidenceQuery(
        query_id="q-readonly",
        source_type=SourceType.CHANGES,
        question="Verify read-only invariants",
    )
    asyncio.run(git_adapter.query(query))

    res_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=git_adapter.repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    count_after = int(res_after.stdout.strip())
    assert count_before == count_after

    status_res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_adapter.repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status_res.stdout.strip() == ""


@pytest.mark.asyncio
async def test_14_no_fixed_scenario_values(available_adapter: SourceAdapter):
    """14. No hardcoded benchmark scenario IDs in collector outputs."""
    query = EvidenceQuery(
        query_id="q-no-scenario",
        source_type=available_adapter.source_type,
        question="Verify no benchmark leakage",
        parameters={"service": "unseen-service-999", "limit": 10},
    )
    result = await available_adapter.query(query)
    result_json = result.model_dump_json()

    forbidden_scenario_strings = [
        "incident_001",
        "incident_002",
        "incident_003",
        "incident_004",
        "incident_005",
        "bad_db_config",
        "oom_kill",
    ]
    for forbidden in forbidden_scenario_strings:
        assert forbidden not in result_json
