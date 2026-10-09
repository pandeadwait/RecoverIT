# Person 2 Handoff: Real Tools & Collection Workstream

## 1. Branch & Commit Information

- **Branch**: `feature/live-collectors`
- **Base**: `origin/langgraph-interface-v1`
- **Final Commit SHA**: `ebf31b57c78bc38b3b840b41bd87381af4cd30c1` (or amended HEAD)

---

## 2. Changed, Added, and Deleted Files

### Added Files (19)
- `collectors/base.py`: `@runtime_checkable` `SourceAdapter` protocol matching §10.1.
- `collectors/registry.py`: `SourceRegistry` and `DuplicateSourceAdapterError` matching §10.2.
- `collectors/service.py`: `DefaultCollectionService`, `Clock`, `IdentifierFactory` matching §10.3.
- `collectors/validation.py`: `validate_query(query, capability)` helper matching §10.3 / §15.4.
- `collectors/changes/local_git.py`: `LocalGitChangeAdapter` (real read-only git log/diff collector).
- `collectors/logs/file.py`: `FileLogAdapter` (real log parser with JSON/regex/bracket parsing).
- `collectors/metrics/prometheus.py`: `PrometheusMetricAdapter` (PromQL matrix/instant query collector).
- `collectors/pipelines/github_actions.py`: `GitHubActionsPipelineAdapter` (workflow runs & failure jobs).
- `collectors/deployments/kubernetes.py`: `KubernetesDeploymentAdapter` (deployments, images, rollouts).
- `collectors/configuration/git_configuration.py`: `GitConfigurationAdapter` (config diffs with secret redaction).
- `collectors/health/http_health.py`: `HttpHealthAdapter` (HTTP health check & latency prober).
- `collectors/health/__init__.py`: Package export for health collectors.
- `tests/support/__init__.py`: Package init for test support tools.
- `tests/support/scripted_source.py`: `ScriptedSourceAdapter` for deterministic multi-workstream tests.
- `tests/collectors/__init__.py`: Test package init.
- `tests/collectors/test_registry.py`: Unit tests for `SourceRegistry`.
- `tests/collectors/test_service.py`: Unit tests for `DefaultCollectionService`.
- `tests/collectors/test_real_adapters.py`: Unit tests for all 7 real read-only adapters.
- `tests/collectors/test_adapter_contract.py`: Shared 14-point contract test suite (§15.4).

### Modified Files (9)
- `collectors/__init__.py`: Exports canonical protocols, real adapters, and backward-compatible services.
- `collectors/changes/__init__.py`: Exports `LocalGitChangeAdapter` and `FixtureChangeAdapter`.
- `collectors/changes/git_adapter.py`: Preserved Phase 3 legacy adapter contract integrity.
- `collectors/configuration/__init__.py`: Exports `GitConfigurationAdapter` and `FixtureConfigurationAdapter`.
- `collectors/deployments/__init__.py`: Exports `KubernetesDeploymentAdapter` and `FixtureDeploymentAdapter`.
- `collectors/logs/__init__.py`: Exports `FileLogAdapter` and `FixtureLogAdapter`.
- `collectors/metrics/__init__.py`: Exports `PrometheusMetricAdapter` and `FixtureMetricAdapter`.
- `collectors/pipelines/__init__.py`: Exports `GitHubActionsPipelineAdapter` and `FixturePipelineAdapter`.
- `docs/contract-change-requests/PERSON_2.md`: Recorded confirmation of 0 contract change requests.

### Deleted Files (0)
- None.

---

## 3. Test Commands and Results

### Test Executions
1. **Shared 14-Point Adapter Contract Test Suite (§15.4)**:
   ```bash
   .\pjtVenv\Scripts\python.exe -m pytest tests/collectors/test_adapter_contract.py -v
   ```
   **Result**: 92 passed in 9.23s.
   Covers all 8 adapters (7 real reference adapters + `ScriptedSourceAdapter`) across all 14 criteria:
   - 1. Capability inspection without mutation or query execution
   - 2. Adapter name identifier stability
   - 3. Supported query fields advertized and enforced
   - 4. Start/end time filtering excludes out-of-window data
   - 5. Service filtering returns records or empty safely
   - 6. Result limit and truncation enforcement
   - 7. Empty successful result does not raise exception
   - 8. Unavailable source returns `UNAVAILABLE` status without crash
   - 9. Timeout behavior handled cleanly
   - 10. Malformed source responses handled gracefully
   - 11. Sensitive secrets redacted from payloads, warnings, and diffs
   - 12. Stable provenance identifiers on all returned records
   - 13. Read-only operation without side-effects or source mutations
   - 14. Real collector isolation (no benchmark fixtures or scenario names in production)

2. **Collector Package Test Suite**:
   ```bash
   .\pjtVenv\Scripts\python.exe -m pytest tests/collectors/ -v
   ```
   **Result**: 111 passed in 9.58s.
   Includes:
   - `test_registry.py`: 5 tests passing (duplicate rejection, unavailable discovery, capability reporting).
   - `test_service.py`: 6 tests passing (semaphore bounded concurrency, query timeout, error isolation).
   - `test_real_adapters.py`: 8 tests passing (real behavior for git, log, prom, github, k8s, config, health).
   - `test_adapter_contract.py`: 92 contract tests passing.

3. **Full Repository Regression Test Suite**:
   ```bash
   .\pjtVenv\Scripts\python.exe -m pytest -q
   ```
   **Result**: 707 passed, 40 subtests passed, 0 failures in 12.76s.
   Zero regressions across all existing unit, integration, and e2e tests.

---

## 4. Dependencies and Environment Variables

### Core Dependencies
- `httpx >= 0.27.0` (asynchronous HTTP client with mock transport support)
- `pydantic >= 2.0.0` (contract validation)

### Environment Variables
| Environment Variable | Target Adapter | Description | Default / Fallback |
|---|---|---|---|
| `PROMETHEUS_ENDPOINT` | `PrometheusMetricAdapter` | Base URL of Prometheus server (e.g. `http://prometheus:9090`) | None (marked unavailable) |
| `PROMETHEUS_BEARER_TOKEN` | `PrometheusMetricAdapter` | Optional Bearer token for Prometheus auth | None |
| `GITHUB_ACTIONS_REPOSITORY` | `GitHubActionsPipelineAdapter` | Repository `owner/repo` | None (marked unavailable) |
| `GITHUB_TOKEN` | `GitHubActionsPipelineAdapter` | GitHub API personal access or workflow token | None |
| `KUBERNETES_API_SERVER_URL` | `KubernetesDeploymentAdapter` | K8s API server URL (e.g. `https://kubernetes.default.svc`) | None |
| `KUBERNETES_BEARER_TOKEN` | `KubernetesDeploymentAdapter` | ServiceAccount bearer token | None |
| `KUBECONFIG` | `KubernetesDeploymentAdapter` | Path to kubeconfig file (fallback if API server not configured) | `~/.kube/config` |
| `HTTP_HEALTH_ENDPOINTS` | `HttpHealthAdapter` | JSON map of `{service: url}` (e.g. `{"order": "http://order/health"}`) | None |
| `HTTP_HEALTH_DEFAULT_URL` | `HttpHealthAdapter` | Fallback health endpoint URL | None |
| `CONFIG_REPO_PATH` | `GitConfigurationAdapter` | Path to git repository holding configs | Current directory |
| `LOCAL_GIT_REPO_PATH` | `LocalGitChangeAdapter` | Path to git repository holding application code | Current directory |
| `LOG_FILE_PATH` | `FileLogAdapter` | Path to application log file | None (marked unavailable) |

---

## 5. Configuration Instructions

### Missing Configuration Safety Rule
When any adapter is initialized with missing or empty target configuration:
- `adapter.get_capability()` returns `available=False` with a clear, sanitized `unavailable_reason`.
- `await adapter.query(query)` returns `SourceResult(source_status=SourceStatus.UNAVAILABLE, records=[])`.
- Production adapters **never** fall back to benchmark fixtures, test scenarios, or synthetic data.

### Initializing Adapters
```python
from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.logs.file import FileLogAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.health.http_health import HttpHealthAdapter

# Real adapters
git_adapter = LocalGitChangeAdapter(repo_path="/path/to/repo", service_name="payment-service")
log_adapter = FileLogAdapter(log_path="/var/log/apps/app.log", service_name="payment-service")
prom_adapter = PrometheusMetricAdapter(endpoint_url="http://prometheus.monitoring:9090")
github_adapter = GitHubActionsPipelineAdapter(repository="org/repo", github_token="ghp_...")
k8s_adapter = KubernetesDeploymentAdapter(api_server_url="https://k8s.internal:6443", bearer_token="...")
config_adapter = GitConfigurationAdapter(repo_path="/path/to/config-repo")
health_adapter = HttpHealthAdapter(endpoints={"payment-service": "http://payment.internal:8080/health"})
```

---

## 6. Contract-Change Requests

None requested. All frozen schemas and 14 criteria were satisfied cleanly without modifying any contract in `contracts/**`.

---

## 7. Known Limitations

1. **Read-Only Enforcement**: All adapters are strictly read-only and execute non-mutating inspections (e.g. `git log`, Prometheus PromQL query, Kubernetes deployment inspection, HTTP GET). No mutating requests or commands are performed.
2. **Kubernetes Dual Path**: `KubernetesDeploymentAdapter` uses in-cluster / direct HTTPS API calls when `api_server_url` is provided. If `api_server_url` is not provided, it falls back to local `kubectl` CLI commands if `kubectl` is installed and a kubeconfig exists.
3. **Secret Redaction**: `GitConfigurationAdapter` automatically scans diff chunks for known secret keys (`password`, `secret`, `token`, `key`, `credential`, `auth`) and redacts values with `[REDACTED]`. Custom proprietary formats outside key-value structures should avoid committing secrets in plaintext.
4. **Isolated Test Adapters**: Test fixture adapters remain in `collectors/fixtures/` and are only used by tests or benchmarks. Production collectors in `collectors/` do not import or reference scenario names.

---

## 8. Example Runtime Composition

```python
import asyncio
from datetime import datetime, timezone
from collectors.registry import SourceRegistry
from collectors.service import DefaultCollectionService
from collectors.changes.local_git import LocalGitChangeAdapter
from collectors.logs.file import FileLogAdapter
from collectors.metrics.prometheus import PrometheusMetricAdapter
from collectors.pipelines.github_actions import GitHubActionsPipelineAdapter
from collectors.deployments.kubernetes import KubernetesDeploymentAdapter
from collectors.configuration.git_configuration import GitConfigurationAdapter
from collectors.health.http_health import HttpHealthAdapter
from contracts.incident.schemas import IncidentSeed
from contracts.collection.schemas import EvidenceQuery, EvidenceQueryPlan
from contracts.enums import Severity, SourceType

async def run_collection():
    # 1. Instantiate registry and register configured adapters
    registry = SourceRegistry()
    registry.register(LocalGitChangeAdapter(repo_path="."))
    registry.register(FileLogAdapter(log_path="app.log"))
    registry.register(PrometheusMetricAdapter(endpoint_url="http://prometheus:9090"))
    registry.register(GitHubActionsPipelineAdapter(repository="myorg/backend"))
    registry.register(KubernetesDeploymentAdapter(api_server_url="https://k8s:6443"))
    registry.register(GitConfigurationAdapter(repo_path="."))
    registry.register(HttpHealthAdapter(endpoints={"api": "http://localhost:8080/health"}))

    # 2. Discover capabilities for incident
    seed = IncidentSeed(
        incident_id="inc-2026-001",
        external_alert_id="alt-001",
        service="checkout",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
        summary="High error rate in checkout flow",
    )
    capabilities = registry.capabilities(seed)

    # 3. Instantiate Collection Service with bounded concurrency
    collection_service = DefaultCollectionService(
        registry=registry,
        max_concurrency=4,
        query_timeout_seconds=10.0,
    )

    # 4. Execute query plan
    plan = EvidenceQueryPlan(
        incident_id=seed.incident_id,
        plan_id="plan-round-1",
        round_number=1,
        queries=[
            EvidenceQuery(
                query_id="q-git",
                source_type=SourceType.CHANGES,
                question="Recent commits to checkout service",
                parameters={"limit": 5},
            ),
            EvidenceQuery(
                query_id="q-health",
                source_type=SourceType.HEALTH,
                question="Current health status of api",
                parameters={"service": "api"},
            ),
        ],
    )
    batch = await collection_service.collect(plan, capabilities)
    print(f"Collected {len(batch.records)} records across {len(batch.results)} queries.")

if __name__ == "__main__":
    asyncio.run(run_collection())
```
