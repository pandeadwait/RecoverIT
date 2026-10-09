# Redundancy and Live-Readiness Audit

**Audited branch:** `codex/langgraph-final`  
**Date:** 2026-10-09  
**Scope:** tracked Python files, configured live CLI/API paths, legacy
benchmark paths, and placeholder/deterministic implementation patterns.

## Executive result

There are **249 tracked Python files**: **181 application/support files** and
**68 test files**. The import graph has **no orphaned non-test implementation
module**. This audit therefore does **not** mark a file `UNUSED`: that would be
incorrect.

The repository does have substantial **non-live legacy code**. It is still
executed by the benchmark runner, old dashboard, compatibility imports, or
tests. High-confidence files now carry a `BENCHMARK-ONLY` or `TEST-ONLY`
top-level marker instead.

| Classification | Count | Meaning |
|---|---:|---|
| Configured server closure | 63 non-initializer modules | Needed by `recoverit.serve` and the live API. |
| Live CLI static closure | 120 | Inflated by eager benchmark imports in `recoverit.cli`. |
| Benchmark/dashboard closure | 117 | Used by scenario replay/evaluation paths. |
| Test-only implementation modules | 5 | Used by tests, not by a production data-flow path. |
| Orphaned implementation modules | **0** | No safe deletion based solely on lack of use. |

## Method and evidence

1. Enumerated all tracked `*.py` files (not Finder metadata or bytecode).
2. Parsed imports using Python AST and followed transitive dependencies from:
   - `recoverit.serve` (configured server)
   - `recoverit.cli` (live and compatibility CLI)
   - `benchmarks.legacy_runner`, `benchmarks.evaluator`, and
     `recoverit.web.app` (benchmark/dashboard)
3. Searched direct scenario/fixture/provider references and inspected duplicate
   collector implementations manually.
4. Verified the branch test suite: **729 passed, 1 warning, 40 subtests
   passed**.

Static reachability is not treated as sufficient proof of live use: Python
package initializers can import a module without the selected command invoking
its behaviour. The classifications below follow the actual entry-point data
flow.

## Configured live path: used code and real collectors

```text
recoverit.serve
  -> recoverit.web.configured
  -> recoverit.composition
  -> recoverit.runner + investigation.graph
  -> canonical collectors, evidence/context, ranking, and live LLM provider
```

The server reaches the configured runtime files, the LangGraph builder/routing
and node modules, canonical contracts, the live reasoning/ranking services, and
these **seven real adapters**:

- `collectors/changes/local_git.py`
- `collectors/logs/file.py`
- `collectors/metrics/prometheus.py`
- `collectors/pipelines/github_actions.py`
- `collectors/deployments/kubernetes.py`
- `collectors/configuration/git_configuration.py`
- `collectors/health/http_health.py`

These are not canned implementations. They respectively execute Git commands,
parse/filter file logs, make Prometheus/GitHub/Kubernetes/HTTP calls, and read
Git configuration. A scan for fixture scenario IDs in these live adapters and
in `recoverit/composition.py` found none. Their tests passed in the suite.

## Test-only files: used, but not production dependencies

The following five modules have no non-test behavioural caller. They remain
useful test support and are labelled `TEST-ONLY`:

- `reasoning/provider/recorded_provider.py`
- `ingestion/alert/ingestor.py`
- `ingestion/alert/repository.py`
- `ingestion/validation/validator.py`
- `evidence/context/queries.py`

Do not delete them while their current tests remain. `recorded_provider.py` is
also eagerly imported by `reasoning/provider/__init__.py`; that is import
hygiene debt, not a reason for a live investigation to use recordings.

## Redundant code that is still used by benchmarks

| Area | Files | Why redundant | Current consumer |
|---|---|---|---|
| Pre-LangGraph orchestration | `investigation/orchestration/orchestrator.py` (1,651 LOC), `state_machine.py` (473 LOC) | LangGraph owns live orchestration/checkpoint routing. | `benchmarks/legacy_runner.py`, compatibility tests |
| Legacy Git adapter | `collectors/changes/git_adapter.py` (228 LOC) | Duplicates `collectors/changes/local_git.py` against older contracts. | Legacy repository scan |
| Legacy log adapter | `collectors/logs/file_adapter.py` (220 LOC) | Duplicates `collectors/logs/file.py` against older contracts. | Legacy repository scan |
| Old collection layer | `collectors/interfaces.py`, `collectors/gateway/collection_service.py`, `ingestion/capabilities/registry.py` | Parallel synchronous interfaces/services predate canonical `SourceAdapter`/`SourceRegistry`. | Legacy runner and tests |
| Schema bridge | `integration_runtime/adapters.py` | Translates Person 1/2/3 schemas rather than using canonical graph contracts. | Legacy runner and integration tests |
| Scenario dashboard | `recoverit/web/app.py` | Uses hard-coded scenario metadata rather than configured incidents. | Benchmark demonstration only |

The old orchestrator/state machine total **2,124 lines**; the two duplicate
adapters add **448 lines**. They are the highest-confidence deletion batch once
the benchmark route is migrated or officially retired.

## Fixed-output / dummy-logic assessment

### Correctly contained deterministic logic

These files intentionally replay fixed inputs and must not be confused with
live collectors:

- `collectors/fixtures/` — five scenario JSON records, data loading, base
  fixture adapter, and replay adapter.
- six `fixture_adapter.py` files below `collectors/{changes,logs,metrics,
  pipelines,deployments,configuration}/`.
- `reasoning/provider/recorded_provider.py` and the compatibility re-export in
  `reasoning/provider/fake_provider.py`.

They are consumed by the benchmark runner. `build_runtime()` does not select
them: it creates only allowlisted real adapters from
`RuntimeSettings.source_configs` and rejects an absent live LLM client.

### Remaining risks to remove after acceptance testing

1. `recoverit/cli.py` imports fixtures and `BenchmarkRunner` at module load
   time for legacy commands. `investigate` does not call them, but currently
   loads them. Move those commands to `benchmarks/cli.py`.
2. `collectors/__init__.py` re-exports fixture and legacy types, so importing a
   `collectors.*` submodule eagerly loads legacy code. Make package exports
   minimal/lazy.
3. `reasoning/provider/__init__.py` eagerly imports fake and recorded
   providers, making a production import depend on test support. Keep only
   live provider/client exports there.
4. `MissingInformationAssessor`, `EvidenceQueryPlanner`, and
   `DefaultHypothesisService` default to a scripted test provider when no
   provider is supplied. Configured composition injects a live LLM, so the
   current entry point is safe; the class defaults should nevertheless fail
   fast in a later cleanup.

## Marking and deletion decision

**No `UNUSED` headers were added because there are zero orphaned implementation
files.** Marking active benchmark/test code as unused would create a dangerous
false signal. Instead, files confirmed to be outside the configured live path
are now labelled `BENCHMARK-ONLY` or `TEST-ONLY`.

No file was deleted today. When the live API acceptance run is complete, use a
dedicated cleanup commit in this order:

1. Move legacy CLI commands into `benchmarks/cli.py` and remove their imports
   from the live CLI.
2. Minimize `collectors/__init__.py` and `reasoning/provider/__init__.py`.
3. Migrate benchmarks to the LangGraph runner with recorded adapters, or
   formally retire the scenario benchmark route.
4. Delete the labelled benchmark-only stack and its tests/fixture data in one
   commit.

That deletion is recoverable: Git retains the current branch history, and the
cleanup commit can be reverted or individual files restored from its parent.
