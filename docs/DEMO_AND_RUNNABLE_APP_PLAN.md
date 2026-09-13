# Architecture & Implementation Plan: Runnable RecoverIT Application

This document outlines the architecture, data flow, and implementation plan for transforming the **RecoverIT** engine from a library into a full, runnable application with live data adapters, a Rich CLI, and an interactive Web Dashboard.

---

## 1. System Architecture & High-Level Flow

RecoverIT does not get imported into target user applications. It operates as an external, autonomous DevOps triage platform:

```
                      +-----------------------------+
                      |   TARGET CODEBASE & INFRA   |
                      |  - Local Git Repo (diffs)   |
                      |  - Application Logs         |
                      |  - Metrics & Deployments    |
                      +--------------+--------------+
                                     |
                                     | (Read-only observation)
                                     v
+------------------------------------+-------------------------------------+
|                          RECOVERIT ENGINE                                |
|                                                                          |
|  1. Alert Ingestion (Person 1)                                           |
|     - Validates and ingests alert -> creates IncidentSeed                |
|                                                                          |
|  2. Source Capability Discovery & Collection (Person 1)                  |
|     - Registers Adapters (Git, Logs, Metrics, Deployments, etc.)         |
|     - Discovers available query fields -> SourceCapabilityCatalog        |
|     - Executes planned queries -> RawEvidenceBatch                       |
|                                                                          |
|  3. Evidence Normalization & Timeline (Person 2)                         |
|     - Redacts credentials/PII and normalizes schemas                     |
|     - Deduplicates repeat alerts and events                              |
|     - Reconstructs chronological event timeline                          |
|     - Publishes IncidentContextSnapshot                                  |
|                                                                          |
|  4. Autonomous Investigation & Hypothesis Ranking (Person 3)             |
|     - Assesses missing information & gaps                                |
|     - Formulates targeted EvidenceQueryPlan                              |
|     - Generates & revises root-cause hypotheses                          |
|     - Validates citations against real evidence in context               |
|     - Deterministically ranks hypotheses with score breakdown            |
+------------------------------------+-------------------------------------+
                                     |
                  +------------------+------------------+
                  |                                     |
                  v                                     v
     +-------------------------+           +--------------------------+
     |   INTERACTIVE WEB UI    |           |    RICH TERMINAL CLI     |
     |   FastAPI + Modern Web  |           |    python -m recoverit   |
     |   - Incident selector   |           |    - Live step spinners  |
     |   - Live agent stepper  |           |    - Colorized timeline  |
     |   - Timeline & diffs    |           |    - Ranked causes table |
     |   - Post-mortem report  |           |    - Post-mortem export  |
     +-------------------------+           +--------------------------+
```

---

## 2. Core Components to Add

### A. Real Data Adapters (`collectors/`)
1. **`LocalGitChangeAdapter`** (`collectors/changes/git_adapter.py`):
   - Implements `ChangeSource`.
   - Inspects a local Git repository on disk using `git log -n {limit} --stat -p`.
   - Extracts commit SHAs, authors, messages, altered files, and exact unified diffs.
   - Emits canonical `RawRecord` instances for the timeline.
2. **`FileLogAdapter`** (`collectors/logs/file_adapter.py`):
   - Implements `LogSource`.
   - Reads `.log`, `.txt`, or JSON log files from disk.
   - Parses timestamps, error levels (`ERROR`, `CRITICAL`), error signatures, and stack traces.

### B. Live LLM Clients (`reasoning/provider/clients.py`)
Implements the `LLMClient` protocol used by `LLMReasoningProvider`:
1. **`OpenAICompatibleClient`**: Calls standard `/v1/chat/completions` endpoints with JSON schema enforcement. Works with OpenAI, Groq, DeepSeek, and local Ollama (`localhost:11434`).
2. **`GeminiClient`**: Calls Google Gemini API with `response_mime_type="application/json"` and Pydantic schema validation.
3. **`AutoLLMClient`**: Factory helper that detects available keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`) or defaults to zero-token offline deterministic mode.

### C. Unified Runner (`recoverit/runner.py`)
Provides an asynchronous API for running investigations:
```python
runner = InvestigationRunner()
# Run a pre-packaged scenario
result = await runner.run_scenario("bad_db_config", mode="auto")

# Or run against an actual folder on disk
result = await runner.run_target(
    repo_path="D:/Projects/sample-service",
    log_path="D:/Projects/sample-service/app.log",
    service="sample-service",
)
```

### D. Rich Terminal CLI (`recoverit/cli.py`)
Accessible via `python -m recoverit.cli`:
- `list`: Lists available scenarios and known incident patterns.
- `run --scenario <name>`: Runs investigation with live spinners, prints a colorized timeline, and displays ranked hypotheses.
- `scan --repo <path> --logs <path>`: Runs triage on a local codebase and log file.
- `report --scenario <name> --out report.md`: Exports a markdown post-mortem.

### E. Interactive Web Dashboard (`recoverit/web/`)
- **Backend**: FastAPI app (`recoverit/web/app.py`) exposing `/api/scenarios`, `/api/investigate`, and `/api/report/{id}`.
- **Frontend**: Single-page modern dashboard (vanilla CSS & JS) with:
  - Incident trigger panel.
  - Animated agent state machine pipeline (Gap Analysis -> Query Planning -> Evidence Collection -> Timeline Construction -> Hypothesis Ranking).
  - Event timeline with syntax-highlighted git diffs.
  - Ranked root-cause hypotheses cards with confidence meters and evidence citations.
  - One-click post-mortem report download.
- **Launcher**: `python -m recoverit.serve` launches Uvicorn and opens the browser.

### F. Demo Sandbox & Presenter Playbook
1. **`examples/demo_service/`**: Realistic demo service with a git repository and commit history containing a configuration bug and matching logs.
2. **`DEMO_GUIDE.md`**: Step-by-step speaker guide with exact commands, clicks, talking points, and key takeaways for evaluators.

---

## 3. Supported Scenarios for Live Demos

| Scenario ID | Service | Incident Summary | Root Cause Category | Key Evidence Shown |
|---|---|---|---|---|
| `bad_db_config` | `payment-api` | HTTP 500 error spike post-deployment | Configuration Regression | Git diff in `config/database.yaml` changing host to misconfigured address; connection pool timeouts |
| `memory_exhaustion` | `order-api` | Pod crash loop with exit code 137 | Resource Exhaustion | Working set memory metric climb; `OOMKilled` logs; worker container restart events |
| `dependency_incompatibility` | `auth-service` | Service failure after build | Dependency Incompatibility | Lockfile update bumping incompatible package version; import error logs |
| `real_db_outage` | `billing-api` | Database connection failures | Database Outage | Primary database failover logs; connection drops across all services without recent deploys |
| `coincidental_deployment` | `checkout-api` | Gateway errors during deployment | External Dependency Failure | Deployment coincided with incident, but cause was third-party gateway downtime. Proves agent avoids false attribution |

---

## 4. Verification & Quality Gates
- All 479 existing unit and integration tests must continue to pass.
- New unit tests for `LocalGitChangeAdapter`, `FileLogAdapter`, and `clients.py`.
- Integration tests for `InvestigationRunner` and FastAPI endpoints.
