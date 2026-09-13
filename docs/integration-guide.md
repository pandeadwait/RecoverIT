# RecoverIT — Source Adapter Integration Guide

This guide explains how to implement, test, and register new operational source adapters (e.g. Prometheus, Loki, GitHub, Datadog, Kubernetes) into the **Incident Intake & Multi-Source Collection** subsystem (Person 1's scope).

---

## 1. Architectural Boundary & Separation of Concerns

Person 1 owns the **boundary layer** between RecoverIT and external infrastructure:
- **Intake**: Validate alerts, guarantee idempotency, generate `IncidentSeed`.
- **Discovery**: Expose source capabilities via `SourceCapabilityCatalog`.
- **Collection**: Execute `EvidenceQueryPlan` queries concurrently via `CollectionService`, returning `RawEvidenceBatch`.

```
                        ┌──────────────────────────────┐
                        │   Incoming Alert / Webhook   │
                        └──────────────┬───────────────┘
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │         AlertIngestor         │
                       └───────────────┬───────────────┘
                                       │
                    Produces: IncidentSeed (to Persons 2 & 3)
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │        SourceRegistry         │
                       └───────────────┬───────────────┘
                                       │
               Produces: SourceCapabilityCatalog (to Person 3)
                                       │
                                       ▼
          Person 3 sends: EvidenceQueryPlan (round of queries)
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │       CollectionService       │
                       └───────┬───────────────┬───────┘
                               │               │ (Concurrent dispatch)
                               ▼               ▼
                       ┌──────────────┐ ┌──────────────┐
                       │ LogSource    │ │ MetricSource │ ... (6 sources)
                       └──────┬───────┘ └──────┬───────┘
                              └────────┬───────┘
                                       ▼
                  Produces: RawEvidenceBatch (to Person 2)
```

### Golden Rules (WORK_DIVISION §6.3, §6.7)
1. **Never interpret root causes or hypotheses in collection code.**
2. **Never include credentials or raw tokens in outputs.**
3. **Preserve original source IDs and timestamps without destructive transformation.**
4. **Source failures or timeouts must never crash the service.**

---

## 2. The Source Adapter Protocol

All source adapters must satisfy the `BaseSource` protocol defined in `collectors.interfaces`:

```python
from typing import Protocol, runtime_checkable
from contracts.collection.batch import QueryResult
from contracts.collection.capabilities import SourceCapability
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import SourceType
from contracts.incident.seed import IncidentSeed

@runtime_checkable
class BaseSource(Protocol):
    source_type: SourceType

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        """Return capabilities: supported fields, limits, and availability."""
        ...

    def query(self, query: EvidenceQuery) -> QueryResult:
        """Execute query against the data source and return a typed QueryResult."""
        ...
```

Specific sub-protocols exist for each evidence category:
- `LogSource` (`source_type = SourceType.LOGS`)
- `MetricSource` (`source_type = SourceType.METRICS`)
- `ChangeSource` (`source_type = SourceType.CHANGES`)
- `DeploymentSource` (`source_type = SourceType.DEPLOYMENTS`)
- `PipelineSource` (`source_type = SourceType.PIPELINES`)
- `ConfigurationSource` (`source_type = SourceType.CONFIGURATION`)

---

## 3. Implementing a New Adapter (Mock API Examples)

Below are complete examples illustrating how to connect to external systems. The examples use mock API clients so you can swap in your real HTTP client (e.g., `httpx`, `requests`, `aiohttp`, or vendor SDK) with minimal changes.

### Example A: Metric Source (e.g. Prometheus / Datadog)

```python
"""Example Prometheus Metric Adapter with Mock API client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import urllib.parse

from collectors.interfaces import MetricSource
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import SourceStatus, SourceType
from contracts.incident.seed import IncidentSeed


class MockPrometheusHttpClient:
    """Mock HTTP client representing requests.get against Prometheus /api/v1/query_range."""

    def __init__(self, base_url: str = "http://prometheus.monitoring:9090"):
        self.base_url = base_url

    def query_range(self, promql: str, start: float, end: float, step: int = 15) -> dict[str, Any]:
        # Production: response = httpx.get(f"{self.base_url}/api/v1/query_range", params=...)
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "http_requests_total", "service": "payment-api"},
                        "values": [[start, "10"], [start + step, "45"], [end, "120"]],
                    }
                ],
            },
        }


class PrometheusMetricAdapter(MetricSource):
    """Production-ready Prometheus metric adapter."""

    source_type: SourceType = SourceType.METRICS
    adapter_name: str = "prometheus-adapter"

    def __init__(self, client: MockPrometheusHttpClient | None = None) -> None:
        self.client = client or MockPrometheusHttpClient()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        return SourceCapability(
            source_type=SourceType.METRICS,
            available=True,
            supported_query_fields=["service", "metric_name", "start_time", "end_time", "aggregation"],
            maximum_window_seconds=86400,  # 24 hours
            maximum_items=1000,
        )

    def query(self, query: EvidenceQuery) -> QueryResult:
        try:
            metric_name = query.parameters.get("metric_name", "http_requests_total")
            service = query.parameters.get("service", "all")
            promql = f'{metric_name}{{service="{service}"}}'

            now_ts = datetime.now(timezone.utc).timestamp()
            raw_data = self.client.query_range(promql, start=now_ts - 300, end=now_ts)

            records: list[RawRecord] = []
            for item in raw_data.get("data", {}).get("result", []):
                records.append(
                    RawRecord(
                        source_record_id=f"prom-{metric_name}-{service}",
                        event_time=datetime.now(timezone.utc),
                        observed_at=datetime.now(timezone.utc),
                        content_type="time_series",
                        payload={"metric": item.get("metric"), "values": item.get("values")},
                    )
                )

            return QueryResult(
                query_id=query.query_id,
                source_type=SourceType.METRICS,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.OK,
                records=records,
            )
        except Exception as exc:
            return QueryResult(
                query_id=query.query_id,
                source_type=SourceType.METRICS,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                records=[],
                warnings=[f"Prometheus query failed: {exc}"],
            )
```

---

### Example B: Change Source (e.g. GitHub / GitLab)

```python
"""Example GitHub Change Adapter with Mock REST client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from collectors.interfaces import ChangeSource
from contracts.collection.batch import QueryResult, RawRecord
from contracts.collection.capabilities import SourceCapability
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import SourceStatus, SourceType
from contracts.incident.seed import IncidentSeed


class MockGitHubClient:
    """Mock representing PyGithub or GitHub REST API /repos/{owner}/{repo}/commits."""

    def get_commits(self, repo: str, since: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        # Production: response = requests.get(f"https://api.github.com/repos/{repo}/commits", ...)
        return [
            {
                "sha": "a1b2c3d4e5f6",
                "author": "sre-engineer@company.com",
                "message": "fix(db): increase connection pool timeout",
                "timestamp": "2026-09-12T10:15:00Z",
                "files": ["src/db/connection.py"],
            }
        ]


class GitHubChangeAdapter(ChangeSource):
    """Production-ready GitHub change adapter."""

    source_type: SourceType = SourceType.CHANGES
    adapter_name: str = "github-adapter"

    def __init__(self, client: MockGitHubClient | None = None) -> None:
        self.client = client or MockGitHubClient()

    def get_capability(self, incident: IncidentSeed | None = None) -> SourceCapability:
        return SourceCapability(
            source_type=SourceType.CHANGES,
            available=True,
            supported_query_fields=["repository", "since", "until", "paths", "max_commits"],
            maximum_window_seconds=604800,  # 7 days
            maximum_items=200,
        )

    def query(self, query: EvidenceQuery) -> QueryResult:
        try:
            repo = query.parameters.get("repository", "org/main-repo")
            limit = query.parameters.get("max_commits", 20)
            commits = self.client.get_commits(repo=repo, limit=limit)

            records = [
                RawRecord(
                    source_record_id=c["sha"],
                    event_time=datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00")),
                    observed_at=datetime.now(timezone.utc),
                    content_type="git_commit",
                    payload=c,
                )
                for c in commits
            ]

            return QueryResult(
                query_id=query.query_id,
                source_type=SourceType.CHANGES,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.OK,
                records=records,
            )
        except Exception as exc:
            return QueryResult(
                query_id=query.query_id,
                source_type=SourceType.CHANGES,
                source_adapter=self.adapter_name,
                source_status=SourceStatus.ERROR,
                records=[],
                warnings=[f"GitHub API query failed: {exc}"],
            )
```

---

## 4. Registering Adapters in `SourceRegistry`

Once an adapter is implemented, register it with the `DefaultSourceRegistry`:

```python
from ingestion.capabilities.registry import DefaultSourceRegistry

registry = DefaultSourceRegistry()

# Register your custom adapters
registry.register_source(PrometheusMetricAdapter())
registry.register_source(GitHubChangeAdapter())

# Source capability catalog will dynamically advertise them
catalog = registry.capabilities(incident_seed)
```

If an adapter is not registered, `SourceRegistry` automatically advertises that source category as `available=False` with zero limits, ensuring the system remains completely stable without crashing.

---

## 5. Wiring into `CollectionService`

To execute multi-source query plans against your adapters:

```python
from collectors.gateway.collection_service import DefaultCollectionService

collection_service = DefaultCollectionService(
    registry=registry,
    max_workers=4,          # Runs independent source queries concurrently
    timeout_seconds=10.0,   # Per-query timeout
)

# Execute plan from Person 3
batch = collection_service.execute(query_plan, catalog)
# batch is a validated RawEvidenceBatch ready for Person 2
```

---

## 6. Writing Contract Tests for a New Adapter

Every new adapter should include a unit test verifying contract compliance:

```python
import pytest
from contracts.collection.batch import QueryResult
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import InformationValue, SourceStatus, SourceType

def test_new_adapter_conforms_to_contract():
    adapter = PrometheusMetricAdapter()

    # 1. Check protocol conformance
    assert adapter.source_type == SourceType.METRICS
    cap = adapter.get_capability()
    assert cap.available is True
    assert cap.maximum_window_seconds > 0

    # 2. Execute sample query
    query = EvidenceQuery(
        query_id="test_q_01",
        source_type=SourceType.METRICS,
        question="Check error metric",
        parameters={"metric_name": "http_requests_total"},
        expected_information_value=InformationValue.HIGH,
    )
    result = adapter.query(query)

    # 3. Validate result against schema
    assert isinstance(result, QueryResult)
    assert result.source_status == SourceStatus.OK
    assert len(result.records) > 0

    # 4. Verify JSON serialization round-trip
    json_data = result.model_dump_json()
    restored = QueryResult.model_validate_json(json_data)
    assert restored == result
```
