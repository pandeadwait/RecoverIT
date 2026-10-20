# RecoverIT repository guide

This guide follows the code as it is organized now. RecoverIT accepts an incident, gathers read-only observations from configured sources, turns those observations into a compact context and timeline, asks a reasoning provider for possible causes, ranks them, and returns a report with human-review remediation guidance. **Start with the story and worked example; use the file inventory as a reference afterward.**

## 1. How the file structure works

This is a Python package repository. The top-level folders are **layers in an investigation**, rather than separate applications:

| Folder | Role |
| --- | --- |
| `contracts/` | Shared data shapes. These define what an incident, query, evidence record, context, hypothesis, and final result mean. |
| `collectors/` | Read-only adapters for logs, Git, CI, Kubernetes, Prometheus, and health endpoints. |
| `evidence/` | Normalizes, redacts, scores, deduplicates, stores, and summarizes collected observations. |
| `timeline/` | Arranges evidence into time-ordered events and temporal relationships. |
| `investigation/` | The LangGraph workflow, its state and nodes, query planning, gap assessment, and budget tracking. |
| `reasoning/` | LLM provider boundary, hypothesis generation/revision, stopping, and deterministic ranking. |
| `remediation/` | Suggests and validates human-reviewed remediation plans. |
| `recoverit/` | Wires the pieces together and exposes CLI, API, browser UI, and report output. |
| `tests/` | Contract, unit, integration, and scenario checks with recorded fixtures. |
| `docs/`, `Report/` | Design history, implementation plans, handoffs, examples, and academic report material. They explain the project but are not the runtime path. |

Files named `__init__.py` mark Python packages and often re-export the public names from sibling modules. `schemas.py` usually contains validated Pydantic data models. `ports.py` and `interface.py` define capabilities a component must provide; `memory.py` is a replaceable in-memory implementation. `service.py` usually coordinates work within one subsystem. `test_*.py` checks the behavior named in its filename. macOS `._*` files, `__pycache__/`, `.pytest_cache/`, and `pjtVenv/` are metadata, caches, or local dependencies, not application source.

## 2. The repository as a story

1. **An incident arrives.** `recoverit/cli.py` reads an incident JSON for terminal use; `recoverit/web/live.py` accepts the same kind of incident over HTTP. `contracts/incident/` validates it as an `IncidentSeed`.
2. **The runtime is assembled.** `recoverit/composition.py` reads trusted runtime settings, creates configured read-only collectors and reasoning services, chooses a checkpoint store, and compiles the graph in `investigation/graph/builder.py`. `recoverit/runner.py` starts or resumes one investigation.
3. **The graph decides what to ask.** Its nodes initialize state, assess missing information, and create a bounded query plan. `investigation/graph/routing.py` decides whether to collect, loop, rank, or finish inconclusively. `investigation/budgets/` prevents unlimited investigation.
4. **Collectors obtain observations.** `collectors/registry.py` advertises available sources; `collectors/service.py` executes compatible queries against the configured adapters. Raw batches follow `contracts/collection/` shapes.
5. **Observations become usable context.** In the current live graph, `evidence/context/builder.py` converts raw records directly into redacted evidence summaries, a timeline, and source coverage in an `IncidentContextSnapshot`. The repo also contains a more extensive processing and persistence pipeline under `evidence/application/`, `evidence/normalization/`, `evidence/deduplication/`, and `timeline/`; that pipeline is tested separately and is **not** wired into the live graph's `DefaultContextBuilder`.
6. **Possible causes are tested.** `reasoning/hypotheses/` generates and revises hypotheses and checks their citations. `reasoning/stopping.py` decides whether there is enough information or another round is worthwhile. `reasoning/ranking/` calculates reproducible scores and confidence details.
7. **A result is delivered.** `remediation/` may create a guarded recommendation. `recoverit/runner.py` converts graph output into a report model; the CLI prints it or saves Markdown, while the API returns JSON and streamed progress for `recoverit/web/static/`.

The central loop is: **assess gaps → plan queries → collect → build context → hypothesize → evaluate stopping**. It repeats only if the stop decision says to continue. A query plan with no queries ends inconclusively. A finished investigation ranks hypotheses; remediation planning is included when that service was configured.

## 3. Important files, in parts

### `recoverit/composition.py` — assembly

- **Settings:** `RuntimeSettings`, `SourceAdapterConfig`, and `load_runtime_settings` validate the JSON configuration. They select sources, LLM provider/model, timeouts, concurrency, and checkpoint path.
- **Runtime lifetime:** `RuntimeContainer` holds the compiled graph, source registry, progress dispatcher, and checkpoint connection. `aclose()` closes the persistent checkpoint backend.
- **Construction:** `build_runtime()` creates an in-memory checkpointer for injected test mode or SQLite checkpointing for live/benchmark mode, then builds the graph. `_build_live_dependencies()` and `_build_source_adapters()` instantiate live services and configured source adapters. This is the place to look when asking, “Which concrete implementation is running?”

### `investigation/graph/` — the control room

- `builder.py` lists all graph nodes and edges. The ordering is the quickest way to understand the whole execution path.
- `state.py` defines checkpoint-safe input, working state, and output. A node reads state and returns updates; state carries the incident, query/batch history, current context and hypotheses, errors, progress, ranking, and remediation.
- `dependencies.py` groups the services nodes call; `ports.py` declares their expected interfaces. `routing.py` makes the two branch decisions after planning and stopping.
- `nodes/*.py` each perform one graph step. This separation keeps branching and orchestration visible instead of burying the workflow inside a single function.

### `evidence/application/service.py` — the evidence pipeline

This is a separate, fuller evidence-processing pipeline. It takes raw collector batches and coordinates normalization into a common shape, secret handling, evidence quality, duplicate detection, persistence, and processing outcomes. Follow its calls into `evidence/normalization/`, `security/`, `deduplication/`, and `repositories/` to understand that pipeline. **Do not assume a live graph request passes through it:** live composition currently injects `DefaultContextBuilder` directly.

### `evidence/context/builder.py` — the reasoning handoff

This file contains two paths. `DefaultContextBuilder` is what the live graph currently calls: it redacts raw records, makes evidence summaries, merges them with the previous context, builds a chronological timeline and source coverage, and returns a new snapshot revision. `ContextSnapshotBuilder` is part of the fuller evidence pipeline: it validates already-processed records and timeline references, sorts them deterministically, and builds a snapshot. The snapshot is the reasoning layer's view of the incident; it is not a raw log dump.

### `reasoning/` — possible causes and confidence

`provider/interface.py` sets the provider contract, `provider/llm_provider.py` requests structured LLM output, and `provider/clients.py` connects to the selected live provider. `hypotheses/` generates and revises candidate causes, merges duplicates, and checks that cited evidence exists and has an appropriate role. `ranking/ranking_engine.py` scores the surviving hypotheses with feature functions and weights; `stopping.py` decides when to stop collecting. The LLM proposes content, while validation and ranking impose deterministic checks.

### `recoverit/runner.py`, `cli.py`, and `web/` — delivery

`InvestigationRunner.run()` invokes the graph with an incident-specific checkpoint thread ID; `resume()` reads and continues a saved run. `InvestigationResult` is the application-facing result and renders the Markdown report. `cli.py` handles terminal arguments and progress display. `serve.py` launches the server; `web/configured.py` owns runtime startup/shutdown and serves the static UI; `web/live.py` defines investigation and stream endpoints. The HTML/CSS/JS files are the browser-facing dashboard.

## 4. Follow one incident through the code

Imagine an alert: **“Checkout API started returning errors shortly after a deployment.”** This is an illustration of the program's path, not a hard-coded scenario.

| Moment | What the program has | What happens next | Where to read |
| --- | --- | --- | --- |
| Input | An incident ID, service, environment, detection time, severity, and summary | Pydantic validates the `IncidentSeed` | `contracts/incident/schemas.py`, `recoverit/cli.py`, `recoverit/web/live.py` |
| Setup | Runtime JSON and configured credentials | Source adapters and LLM client are constructed; graph and checkpoint store are opened | `recoverit/composition.py` |
| First graph state | Incident, capabilities, budget | `initialize` creates an empty context (revision 0), empty histories, and round 1 | `investigation/graph/nodes/initialize.py`, `state.py` |
| Questions | Current context and available sources | Gap assessment asks what is unknown; query planning chooses supported, bounded source queries | `nodes/assess.py`, `nodes/plan.py`, `investigation/query_planning/planner.py` |
| Observations | A plan such as “look at recent deployment and error logs” | Collection service calls the matching adapters and returns a `RawEvidenceBatch` | `nodes/collect.py`, `collectors/service.py`, `collectors/*/` |
| Context | Raw records plus the previous snapshot | Live `DefaultContextBuilder` redacts and summarizes records, deduplicates by evidence ID, adds timeline events, and publishes the next revision | `nodes/build_context.py`, `evidence/context/builder.py` |
| Possible causes | Incident and context snapshot | The provider proposes causes; the hypothesis service validates citations and removes near duplicates | `nodes/hypothesize.py`, `reasoning/hypotheses/` |
| Decision | Hypotheses, remaining gaps, budget usage | Stopping rules choose another round, final ranking, or inconclusive finish | `nodes/evaluate.py`, `reasoning/stopping.py`, `routing.py` |
| Output | Ranked result and optional remediation plan | Runner forms the final result; CLI/API/UI present it | `nodes/rank.py`, `nodes/remediate.py`, `recoverit/runner.py`, `recoverit/web/` |

### The objects passed between layers

Think of the contracts as labeled envelopes. They make it possible for a collector, graph node, and report renderer to agree on the same data without sharing implementation details.

```text
IncidentSeed
   + SourceCapabilityCatalog + InvestigationBudget
   -> EvidenceQueryPlan
   -> RawEvidenceBatch
   -> IncidentContextSnapshot (evidence summaries + timeline + coverage)
   -> HypothesisSet
   -> StopDecision -> another round OR RankedHypothesisSet
   -> RemediationPlan (when configured)
   -> InvestigationResult -> Markdown / JSON / browser UI
```

- **IncidentSeed:** the case being investigated. The `incident_id` also identifies its graph checkpoint, so a run can be resumed.
- **SourceCapabilityCatalog:** what the configured sources can actually answer. A source may be absent or unavailable; planning must respect that.
- **EvidenceQueryPlan:** a list of bounded questions for particular source types. It is a request, not collected data.
- **RawEvidenceBatch:** the observations returned by collectors, including source status or errors. A successful query can still return no useful records.
- **IncidentContextSnapshot:** a versioned, compact picture of the case. New rounds retain earlier evidence and add new material instead of starting from scratch.
- **HypothesisSet:** candidate explanations with cited evidence and open questions. It can be revised in later rounds.
- **StopDecision:** one of continue, rank, or finish inconclusively. The graph routing code acts on it.
- **RankedHypothesisSet:** the final ordering with calculated evidence scores and confidence. A rank is a comparison based on available evidence, not proof of causation.
- **RemediationPlan:** operator guidance based on the result. Safety policy can withhold a recommendation and return a blocked plan.

### What one graph node actually does

A node receives `InvestigationGraphState` and its injected services, then returns only the fields it updates. For example, `collect_evidence` reads `query_plan` and `source_capabilities`, calls the collection service, and returns `latest_batch`, extended `batch_history`, increased query usage, and a progress event. The next node reads `latest_batch`. This is why `state.py` is worth keeping open while reading `nodes/`.

`routing.py` contains the branch rules. After planning, **zero queries → inconclusive**; otherwise collect. After stopping evaluation, **continue → assess gaps again**, **rank → calculate scores**, and the remaining stop action → inconclusive. `builder.py` connects these steps and optionally appends remediation planning.

### A crucial distinction: two evidence paths

The repository has a mature set of evidence modules, but the live graph's dependency assembly currently chooses the simpler `DefaultContextBuilder` in `recoverit/composition.py`:

```text
Live graph: RawEvidenceBatch -> DefaultContextBuilder -> IncidentContextSnapshot

Separate evidence subsystem:
RawEvidenceBatch -> normalization -> security -> deduplication
                 -> repositories -> timeline services -> ContextSnapshotBuilder
```

The second path has detailed tests and useful components, but reading it as if every live API request traverses it would give a false picture. If you plan to integrate the fuller pipeline, the seam is the `ContextBuilder` dependency injected by `composition.py` and called in `nodes/build_context.py`.

## 5. Where to look when something changes

| If you want to… | Start here | Then inspect |
| --- | --- | --- |
| Add a new source (for example, another log provider) | `collectors/base.py`, an existing adapter | `collectors/specs.py`, `collectors/registry.py`, `_build_source_adapters()` in `recoverit/composition.py`, collector tests |
| Change what incident JSON accepts | `contracts/incident/schemas.py` | CLI/API input handling and contract tests |
| Change what the investigator asks for | `investigation/query_planning/planner.py` | `nodes/plan.py`, source capabilities, query-plan contracts |
| Change loop or finish behavior | `reasoning/stopping.py` | `nodes/evaluate.py`, `routing.py`, `builder.py`, graph tests |
| Change how raw records become live context | `evidence/context/builder.py` (`DefaultContextBuilder`) | `nodes/build_context.py`, context tests |
| Change secret treatment in the fuller evidence pipeline | `evidence/security/` | `evidence/application/service.py`, security tests |
| Change hypothesis quality | `reasoning/hypotheses/` | provider interface, citation tests, ranking tests |
| Change score or confidence calculations | `reasoning/ranking/feature_calculators.py` | `ranking_engine.py`, `default_weights.json`, ranking tests |
| Change remediation rules | `remediation/policies.py` | `remediation/planner.py`, remediation contracts and tests |
| Change terminal output | `recoverit/cli.py`, `recoverit/runner.py` | CLI/integration tests |
| Change API or browser behavior | `recoverit/web/live.py`, `configured.py` | `web/static/`, live API tests |

## 6. File-by-file map

The list below covers the project's source, tests, fixtures, configuration, and documentation. The one-line entries tell you **why each file exists**; use the sections above for the important paths.

### Root files

- `.gitignore` — Lists local/generated paths Git should ignore.
- `README.md` — Setup, run commands, and a repository map.
- `pyproject.toml` — Defines package metadata, dependencies, package discovery, and pytest settings.
- `setup.py` — Filters macOS AppleDouble metadata from Python distribution builds.

### Shared contracts (`contracts/`)

- `contracts/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/collection/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/collection/batch.py` — RawEvidenceBatch — collected raw evidence from source adapters.
- `contracts/collection/capabilities.py` — SourceCapabilityCatalog — runtime declaration of available sources.
- `contracts/collection/query_plan.py` — Compatibility imports for the canonical collection query contracts.
- `contracts/collection/schemas.py` — Collection contracts.
- `contracts/common.py` — Shared validation, immutability, and deterministic serialization helpers.
- `contracts/context/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/enums.py` — Canonical controlled values shared by every RecoverIT boundary.
- `contracts/errors/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/errors/schemas.py` — Structured error schemas.
- `contracts/evidence/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/evidence/schemas.py` — Evidence contracts.
- `contracts/hypothesis/__init__.py` — Marks this directory as a Python package.
- `contracts/hypothesis/schemas.py` — Hypothesis contracts.
- `contracts/incident/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/incident/alert.py` — IncidentAlert — the external input that triggers an investigation.
- `contracts/incident/schemas.py` — Incident contracts.
- `contracts/incident/seed.py` — IncidentSeed — validated incident record produced from an alert.
- `contracts/investigation/__init__.py` — Marks this directory as a Python package.
- `contracts/investigation/schemas.py` — Investigation contracts.
- `contracts/primitives.py` — Deterministic ports and reference primitives used by later phases.
- `contracts/remediation/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `contracts/remediation/schemas.py` — Canonical contracts for recommendation-only remediation.
- `contracts/timeline/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.

### Source collection (`collectors/`)

- `collectors/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/base.py` — Base protocol for all operational data source adapters.
- `collectors/changes/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/changes/local_git.py` — Local Git repository change adapter for live codebase inspection.
- `collectors/configuration/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/configuration/git_configuration.py` — Git configuration adapter for live versioned configuration inspection.
- `collectors/deployments/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/deployments/kubernetes.py` — Kubernetes deployment adapter for live deployment and rollout inspection.
- `collectors/health/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/health/http_health.py` — HTTP health adapter for live endpoint health and availability inspection.
- `collectors/logs/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/logs/file.py` — File-based log source adapter for live application log inspection.
- `collectors/metrics/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/metrics/prometheus.py` — Prometheus metric adapter for live timeseries metric inspection.
- `collectors/pipelines/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `collectors/pipelines/github_actions.py` — GitHub Actions pipeline adapter for live CI/CD pipeline run inspection.
- `collectors/registry.py` — Source registry managing source adapter registration and capability catalogs.
- `collectors/service.py` — Collection service coordinating concurrent, bounded evidence collection.
- `collectors/specs.py` — Standard capability specifications and factory for operational sources.
- `collectors/validation.py` — Query validation for operational evidence collection.

### Evidence processing (`evidence/`)

- `evidence/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/application/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/application/models.py` — Application-level results for evidence ingestion.
- `evidence/application/observability.py` — Safe structured observability port and reference sinks.
- `evidence/application/service.py` — Phase 6 evidence normalization, security, deduplication, and persistence.
- `evidence/context/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/context/builder.py` — Deterministic construction of Person 2's context snapshot contract.
- `evidence/context/coverage.py` — Deterministic source-coverage calculation from collection contracts.
- `evidence/context/queries.py` — TEST-ONLY context/evidence read contracts exposed to Person 3.
- `evidence/context/repositories/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/context/repositories/memory.py` — Thread-safe in-memory reference context repository.
- `evidence/context/repositories/ports.py` — Domain-owned context snapshot persistence contract.
- `evidence/context/service.py` — Context publication orchestration over domain-owned repository ports.
- `evidence/deduplication/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/deduplication/identity.py` — Replaceable deterministic evidence-ID strategy.
- `evidence/deduplication/models.py` — Immutable, safe audit values for deterministic deduplication.
- `evidence/deduplication/policy.py` — Configurable, non-revealing matching and log aggregation rules.
- `evidence/deduplication/repositories/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/deduplication/repositories/memory.py` — Thread-safe in-memory deduplication-state repository.
- `evidence/deduplication/repositories/ports.py` — Persistence port for mutable deduplication sidecars and provenance links.
- `evidence/deduplication/service.py` — Deterministic, storage-neutral deduplication and repeated-log aggregation.
- `evidence/normalization/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/normalization/classifiers.py` — Source classifier port, registry, and six deterministic classifiers.
- `evidence/normalization/identity.py` — Replaceable identity resolution port and configuration-backed adapter.
- `evidence/normalization/models.py` — Source-neutral values produced and consumed within normalization.
- `evidence/normalization/service.py` — Batch orchestration for classification and canonical normalization.
- `evidence/normalization/timestamps.py` — Timestamp normalization with explicit uncertainty and original metadata.
- `evidence/quality/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/quality/policies.py` — Explainable reliability, freshness, truncation, and skew rules.
- `evidence/repositories/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/repositories/_ordering.py` — Shared deterministic evidence filtering and ordering behavior.
- `evidence/repositories/coordinator.py` — Atomic persistence coordinator port used by application services.
- `evidence/repositories/memory.py` — Thread-safe in-memory reference adapter for the evidence repository port.
- `evidence/repositories/ports.py` — Domain-owned evidence persistence contracts.
- `evidence/repositories/state.py` — Shared state used by the in-memory adapters and atomic coordinator.
- `evidence/repositories/unit_of_work.py` — Atomic repository coordinator port and in-memory reference implementation.
- `evidence/security/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `evidence/security/models.py` — Safe values produced by the Phase 4 security boundary.
- `evidence/security/provenance.py` — Raw-payload hashing and provenance construction.
- `evidence/security/redaction.py` — Recursive field and free-text redaction with quarantine decisions.
- `evidence/security/service.py` — Orchestrates hashing, redaction, quarantine, provenance, and quality.

### Timeline (`timeline/`)

- `timeline/__init__.py` — Marks this directory as a Python package.
- `timeline/builder/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `timeline/builder/mapper.py` — Deterministic mapping from immutable evidence to timeline events.
- `timeline/builder/models.py` — Timeline-builder configuration and validation errors.
- `timeline/builder/service.py` — Evidence-to-timeline construction and replaceable revision persistence.
- `timeline/builder/validation.py` — Graph integrity validation for deterministic Person 2 timelines.
- `timeline/relationships/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `timeline/relationships/service.py` — Deterministic, non-causal temporal relationship calculation.
- `timeline/repositories/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `timeline/repositories/memory.py` — Thread-safe in-memory reference timeline repository.
- `timeline/repositories/ports.py` — Domain-owned timeline persistence contract.

### Investigation workflow (`investigation/`)

- `investigation/__init__.py` — Marks this directory as a Python package.
- `investigation/budgets/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `investigation/budgets/budget_tracker.py` — Investigation budget tracking and limit enforcement.
- `investigation/graph/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `investigation/graph/builder.py` — Build the approved RecoverIT investigation workflow with LangGraph.
- `investigation/graph/dependencies.py` — Runtime-only dependencies injected into investigation graph nodes.
- `investigation/graph/nodes/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `investigation/graph/nodes/assess.py` — Assess known facts and unresolved information gaps.
- `investigation/graph/nodes/build_context.py` — Normalize evidence and publish the next context revision.
- `investigation/graph/nodes/collect.py` — Execute a validated query plan through the collection boundary.
- `investigation/graph/nodes/common.py` — Small shared helpers for graph nodes.
- `investigation/graph/nodes/evaluate.py` — Apply deterministic investigation stopping rules.
- `investigation/graph/nodes/finish.py` — Produce an explicit inconclusive terminal result.
- `investigation/graph/nodes/hypothesize.py` — Generate or revise evidence-linked hypotheses.
- `investigation/graph/nodes/initialize.py` — Initialize checkpoint-safe investigation state.
- `investigation/graph/nodes/plan.py` — Plan source-neutral evidence queries.
- `investigation/graph/nodes/rank.py` — Calculate the final deterministic hypothesis ranking.
- `investigation/graph/nodes/remediate.py` — Remediation planning graph node for operator guidance.
- `investigation/graph/ports.py` — Frozen service boundaries consumed by the LangGraph workflow.
- `investigation/graph/routing.py` — Pure conditional-edge routing for the investigation graph.
- `investigation/graph/state.py` — Serializable state exchanged by RecoverIT LangGraph nodes.
- `investigation/missing_information/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `investigation/missing_information/assessor.py` — Missing information assessment component.
- `investigation/query_planning/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `investigation/query_planning/planner.py` — Evidence query planner component.

### Reasoning (`reasoning/`)

- `reasoning/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `reasoning/hypotheses/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `reasoning/hypotheses/citation_validator.py` — Evidence citation validation component.
- `reasoning/hypotheses/deduplicator.py` — Hypothesis deduplication component.
- `reasoning/hypotheses/generator.py` — Hypothesis generator component.
- `reasoning/hypotheses/reviser.py` — Hypothesis reviser component.
- `reasoning/hypotheses/service.py` — Canonical HypothesisService satisfying the frozen graph protocol.
- `reasoning/provider/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `reasoning/provider/clients.py` — Concrete live LLM client adapters implementing the LLMClient protocol.
- `reasoning/provider/fake_provider.py` — TEST-ONLY compatibility shim for FakeReasoningProvider.
- `reasoning/provider/interface.py` — ReasoningProvider interface definition.
- `reasoning/provider/llm_provider.py` — LLM Reasoning Provider adapter implementation.
- `reasoning/provider/recorded_provider.py` — TEST-ONLY deterministic recorded reasoning provider.
- `reasoning/ranking/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `reasoning/ranking/default_weights.json` — Default numeric weights used by deterministic hypothesis ranking.
- `reasoning/ranking/feature_calculators.py` — Deterministic feature calculators for hypothesis ranking.
- `reasoning/ranking/ranking_engine.py` — Deterministic ranking engine for root-cause hypotheses.
- `reasoning/stopping.py` — Deterministic investigation stopping service.

### Remediation (`remediation/`)

- `remediation/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `remediation/planner.py` — Remediation planning service implementations.
- `remediation/policies.py` — Deterministic safety policies and validation for remediation plans.

### Application and UI (`recoverit/`)

- `recoverit/__init__.py` — Marks this directory as a Python package.
- `recoverit/cli.py` — Rich terminal command-line interface for RecoverIT.
- `recoverit/composition.py` — Runtime composition primitives for LangGraph investigations.
- `recoverit/runner.py` — Fixture-free application runner for LangGraph investigations.
- `recoverit/serve.py` — Configured production server launcher for RecoverIT's LangGraph API.
- `recoverit/web/__init__.py` — Marks this directory as a Python package.
- `recoverit/web/configured.py` — Configured production factory for the fixture-free RecoverIT HTTP API.
- `recoverit/web/live.py` — Fixture-free HTTP interface for a composed LangGraph investigation runtime.
- `recoverit/web/static/app.js` — Browser actions: submit/resume investigations and render API/progress results.
- `recoverit/web/static/index.html` — Browser dashboard structure and controls.
- `recoverit/web/static/style.css` — Dashboard layout, typography, and visual styling.

### Tests and fixtures (`tests/`)

- `tests/__init__.py` — Marks this directory as a Python package.
- `tests/collectors/test_adapter_contract.py` — Checks adapter contract behavior and edge cases.
- `tests/collectors/test_real_adapters.py` — Checks real adapters behavior and edge cases.
- `tests/collectors/test_registry.py` — Checks registry behavior and edge cases.
- `tests/collectors/test_service.py` — Checks service behavior and edge cases.
- `tests/contract/__init__.py` — Marks this directory as a Python package.
- `tests/contract/test_contracts.py` — Checks contracts behavior and edge cases.
- `tests/contract/test_langgraph_interfaces.py` — Checks langgraph interfaces behavior and edge cases.
- `tests/contract/test_schemas.py` — Checks schemas behavior and edge cases.
- `tests/contracts/test_remediation_contracts.py` — Checks remediation contracts behavior and edge cases.
- `tests/evidence/test_context_builder.py` — Checks context builder behavior and edge cases.
- `tests/fixtures/application/persisted_log_record.json` — Recorded persisted log record data used by tests.
- `tests/fixtures/context/phase_8_context_snapshot.json` — Recorded phase 8 context snapshot data used by tests.
- `tests/fixtures/contracts/evidence_filter.json` — Recorded evidence filter data used by tests.
- `tests/fixtures/contracts/evidence_provenance.json` — Recorded evidence provenance data used by tests.
- `tests/fixtures/contracts/evidence_quality.json` — Recorded evidence quality data used by tests.
- `tests/fixtures/contracts/evidence_record.json` — Recorded evidence record data used by tests.
- `tests/fixtures/contracts/incident_context_snapshot.json` — Recorded incident context snapshot data used by tests.
- `tests/fixtures/contracts/incident_seed.json` — Recorded incident seed data used by tests.
- `tests/fixtures/contracts/invalid_schema_version.json` — Recorded invalid schema version data used by tests.
- `tests/fixtures/contracts/invalid_timezone.json` — Recorded invalid timezone data used by tests.
- `tests/fixtures/contracts/processing_error.json` — Recorded processing error data used by tests.
- `tests/fixtures/contracts/processing_warning.json` — Recorded processing warning data used by tests.
- `tests/fixtures/contracts/raw_evidence_batch.json` — Recorded raw evidence batch data used by tests.
- `tests/fixtures/contracts/temporal_relationship.json` — Recorded temporal relationship data used by tests.
- `tests/fixtures/contracts/timeline.json` — Recorded timeline data used by tests.
- `tests/fixtures/contracts/timeline_event.json` — Recorded timeline event data used by tests.
- `tests/fixtures/normalization/all_sources_batch.json` — Recorded all sources batch data used by tests.
- `tests/fixtures/normalization/normalized_candidates.json` — Recorded normalized candidates data used by tests.
- `tests/fixtures/person3/recorded_session_01.json` — Recorded recorded session 01 data used by tests.
- `tests/fixtures/replay/phase_9_scenarios.json` — Recorded phase 9 scenarios data used by tests.
- `tests/fixtures/security/synthetic_secrets.json` — Recorded synthetic secrets data used by tests.
- `tests/fixtures/timeline/phase_7_built_timeline.json` — Recorded phase 7 built timeline data used by tests.
- `tests/graph/__init__.py` — Marks this directory as a Python package.
- `tests/graph/test_investigation_graph.py` — Checks investigation graph behavior and edge cases.
- `tests/graph/test_investigation_graph_remediation.py` — Checks investigation graph remediation behavior and edge cases.
- `tests/graph/test_remediation_node.py` — Checks remediation node behavior and edge cases.
- `tests/integration/test_configured_entrypoints.py` — Checks configured entrypoints behavior and edge cases.
- `tests/integration/test_live_web_api.py` — Checks live web api behavior and edge cases.
- `tests/integration/test_remediation_runner.py` — Checks remediation runner behavior and edge cases.
- `tests/investigation/__init__.py` — Marks this directory as a Python package.
- `tests/investigation/test_budget_tracker.py` — Checks budget tracker behavior and edge cases.
- `tests/investigation/test_missing_information.py` — Checks missing information behavior and edge cases.
- `tests/investigation/test_query_planning.py` — Checks query planning behavior and edge cases.
- `tests/reasoning/__init__.py` — Marks this directory as a Python package.
- `tests/reasoning/test_hypotheses.py` — Checks hypotheses behavior and edge cases.
- `tests/reasoning/test_live_clients.py` — Checks live clients behavior and edge cases.
- `tests/reasoning/test_llm_provider.py` — Checks llm provider behavior and edge cases.
- `tests/reasoning/test_providers.py` — Checks providers behavior and edge cases.
- `tests/reasoning/test_ranking.py` — Checks ranking behavior and edge cases.
- `tests/reasoning/test_stopping_service.py` — Checks stopping service behavior and edge cases.
- `tests/reasoning/test_unseen_scenarios.py` — Checks unseen scenarios behavior and edge cases.
- `tests/remediation/__init__.py` — Marks this directory as a Python package.
- `tests/remediation/test_planner.py` — Checks planner behavior and edge cases.
- `tests/remediation/test_policies.py` — Checks policies behavior and edge cases.
- `tests/repository_contract.py` — Reusable behavior checks that repository implementations must pass.
- `tests/support/__init__.py` — Marks this directory as a Python package and re-exports its public types/functions.
- `tests/support/scripted_reasoning_provider.py` — Support code for test scenarios.
- `tests/support/scripted_remediation_planner.py` — Support code for test scenarios.
- `tests/support/scripted_source.py` — Support code for test scenarios.
- `tests/test_context_publication.py` — Checks context publication behavior and edge cases.
- `tests/test_contracts.py` — Checks contracts behavior and edge cases.
- `tests/test_deduplication.py` — Checks deduplication behavior and edge cases.
- `tests/test_evidence_processing.py` — Checks evidence processing behavior and edge cases.
- `tests/test_in_memory_repositories.py` — Checks in memory repositories behavior and edge cases.
- `tests/test_normalization.py` — Checks normalization behavior and edge cases.
- `tests/test_phase_9_integration.py` — Checks phase 9 integration behavior and edge cases.
- `tests/test_security.py` — Checks security behavior and edge cases.
- `tests/test_timeline_builder.py` — Checks timeline builder behavior and edge cases.
- `tests/unit/__init__.py` — Marks this directory as a Python package.
- `tests/unit/test_citation_causality.py` — Checks citation causality behavior and edge cases.
- `tests/unit/test_evidence_planning.py` — Checks evidence planning behavior and edge cases.
- `tests/unit/test_explainable_confidence.py` — Checks explainable confidence behavior and edge cases.
- `tests/unit/test_hypothesis_deduplication.py` — Checks hypothesis deduplication behavior and edge cases.

### Project documentation (`docs/`)

The [documentation index](../INDEX.md) groups these files by purpose.

- `docs/._INDEX.md` — Project documentation.
- `docs/INDEX.md` — Project documentation.
- `docs/architecture/ARCHITECTURE.md` — Project documentation.
- `docs/architecture/Autonomous_CICD_Incident_Triager_Self_Healer (1).md` — Project documentation.
- `docs/contract-change-requests/PERSON_1.md` — Project documentation.
- `docs/contract-change-requests/PERSON_2.md` — Project documentation.
- `docs/contract-change-requests/PERSON_3.md` — Project documentation.
- `docs/contract-change-requests/README.md` — Project documentation.
- `docs/guides/._REPOSITORY_GUIDE.md` — Project documentation.
- `docs/guides/CLEANUP_AUDIT.md` — Project documentation.
- `docs/guides/CONTRACT_COMPATIBILITY.md` — Project documentation.
- `docs/guides/REPOSITORY_GUIDE.md` — Project documentation.
- `docs/guides/TEAM_SETUP_AND_LIVE_RUN.md` — Project documentation.
- `docs/guides/integration-guide.md` — Project documentation.
- `docs/handoffs/PERSON_1_REMEDIATION_HANDOFF.md` — Project documentation.
- `docs/handoffs/PERSON_2_HANDOFF.md` — Project documentation.
- `docs/handoffs/PERSON_2_REMEDIATION_HANDOFF.md` — Project documentation.
- `docs/handoffs/PERSON_3_HANDOFF.md` — Project documentation.
- `docs/phases/PHASE_1_TESTING.md` — Project documentation.
- `docs/phases/PHASE_2_REPOSITORIES.md` — Project documentation.
- `docs/phases/PHASE_3_NORMALIZATION.md` — Project documentation.
- `docs/phases/PHASE_4_SECURITY.md` — Project documentation.
- `docs/phases/PHASE_5_DEDUPLICATION.md` — Project documentation.
- `docs/phases/PHASE_6_EVIDENCE_PROCESSING.md` — Project documentation.
- `docs/phases/PHASE_7_TIMELINE.md` — Project documentation.
- `docs/phases/PHASE_8_CONTEXT.md` — Project documentation.
- `docs/phases/PHASE_9_HANDOFF.md` — Project documentation.
- `docs/plans/CREDIBILITY_IMPROVEMENT_PLAN.md` — Project documentation.
- `docs/plans/LANGGRAPH_MIGRATION_AND_TEAM_EXECUTION_PLAN.md` — Project documentation.
- `docs/plans/PERSON_2_IMPLEMENTATION_PLAN.md` — Project documentation.
- `docs/plans/PERSON_3_IMPLEMENTATION_PLAN.md` — Project documentation.
- `docs/plans/REMEDIATION_RECOMMENDATION_IMPLEMENTATION_PLAN.md` — Project documentation.
- `docs/plans/REMEDIATION_TWO_PERSON_EXECUTION_PLAN.md` — Project documentation.
- `docs/plans/WORK_DIVISION.md` — Project documentation.
- `docs/plans/implementation_plan.md` — Project documentation.
- `docs/report/REPORT_OUTLINE.md` — Project documentation.
- `docs/incident.example.json` — Example incident input for a configured investigation.
- `docs/live-runtime.example.json` — Example source, provider, and checkpoint configuration.

### Report and presentation material (`Report/`)

- `Report/2026-10-15_15-01-23.png` — Screenshot included with report material.
- `Report/2026-10-15_15-01-54.png` — Screenshot included with report material.
- `Report/2026-10-15_15-02-29.png` — Screenshot included with report material.
- `Report/2026-10-15_15-02-49.png` — Screenshot included with report material.
- `Report/Autonomous CI_CD Incident Triager & Self-Healer.pdf` — PDF report/reference: Autonomous CI_CD Incident Triager & Self-Healer.
- `Report/BCE497J PROJECT REPORT TEMPLATE.docx` — Editable report/reference document: BCE497J PROJECT REPORT TEMPLATE.
- `Report/BCSE497J_Project_I_Guidelines.pdf` — PDF report/reference: BCSE497J_Project_I_Guidelines.
- `Report/CHAPTER 1.docx` — Editable report/reference document: CHAPTER 1.
- `Report/Integrated CICD Incident Triager.pdf` — PDF report/reference: Integrated CICD Incident Triager.
- `Report/Literature Reviews.pdf` — PDF report/reference: Literature Reviews.
- `Report/RecoverIT Project I Presentation - Updated.pptx` — Presentation deck: RecoverIT Project I Presentation - Updated.
- `Report/RecoverIT Project I Presentation.pptx` — Presentation deck: RecoverIT Project I Presentation.
- `Report/RecoverIT Report II Team Revision II.docx` — Editable report/reference document: RecoverIT Report II Team Revision II.
- `Report/RecoverIT Report II Team Revision.docx` — Editable report/reference document: RecoverIT Report II Team Revision.
- `Report/RecoverIT Report II.docx` — Editable report/reference document: RecoverIT Report II.
- `Report/RecoverIT Report II.pdf` — PDF report/reference: RecoverIT Report II.
- `Report/RecoverIT Report III.docx` — Editable report/reference document: RecoverIT Report III.
- `Report/RecoverIT Report III.pdf` — PDF report/reference: RecoverIT Report III.
- `Report/RecoverIT Report IV (Adwait).docx` — Editable report/reference document: RecoverIT Report IV (Adwait).
- `Report/RecoverIT Report IV (Adwait).pdf` — PDF report/reference: RecoverIT Report IV (Adwait).
- `Report/RecoverIT Report IV (Om).docx` — Editable report/reference document: RecoverIT Report IV (Om).
- `Report/RecoverIT Report IV (Om).pdf` — PDF report/reference: RecoverIT Report IV (Om).
- `Report/RecoverIT Report IV (Suryansh).docx` — Editable report/reference document: RecoverIT Report IV (Suryansh).
- `Report/RecoverIT Report IV (Suryansh).pdf` — PDF report/reference: RecoverIT Report IV (Suryansh).
- `Report/RecoverIT Report IV Content (1).docx` — Editable report/reference document: RecoverIT Report IV Content (1).
- `Report/RecoverIT Report IV Content.docx` — Editable report/reference document: RecoverIT Report IV Content.
- `Report/RecoverIT Report IV Content.docx.pdf` — PDF report/reference: RecoverIT Report IV Content.docx.
- `Report/RecoverIT Report IV.docx` — Editable report/reference document: RecoverIT Report IV.
- `Report/RecoverIT Report.docx` — Editable report/reference document: RecoverIT Report.
- `Report/RecoverIT Report.pdf` — PDF report/reference: RecoverIT Report.
- `Report/RecoverIT — Autonomous CI_CD Incident Triager & Self-Healer.pptx` — Presentation deck: RecoverIT — Autonomous CI_CD Incident Triager & Self-Healer.
- `Report/RecoverIT_Project_I_Report.docx` — Editable report/reference document: RecoverIT_Project_I_Report.
- `Report/RecoverIT_Project_I_Report_phrased.docx` — Editable report/reference document: RecoverIT_Project_I_Report_phrased.
- `Report/RecoverIT_Project_I_Report_phrased_testing.docx` — Editable report/reference document: RecoverIT_Project_I_Report_phrased_testing.
- `Report/RecoverIT_Report_II_Team_Voice.docx` — Editable report/reference document: RecoverIT_Report_II_Team_Voice.

## 7. A practical reading order

For a first pass, read `README.md`, `recoverit/composition.py`, `investigation/graph/builder.py`, `investigation/graph/state.py`, and the graph nodes in execution order. Then follow `collectors/service.py` into the live `DefaultContextBuilder` in `evidence/context/builder.py`. Finish with `reasoning/hypotheses/service.py`, `reasoning/ranking/ranking_engine.py`, `remediation/planner.py`, and `recoverit/runner.py`. Read `evidence/application/service.py` and `timeline/` separately when studying the fuller evidence subsystem. The tests next to each area show concrete inputs and expected outputs.

This inventory is based on the repository files present on 20 October 2026. The `Report/` directory includes local untracked documents as well as committed material; those are described by their filenames because they are project deliverables rather than executable code.
