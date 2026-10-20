# LangGraph Cleanup Audit

## Scope

This branch removes only code that was unreachable from the configured
LangGraph runtime and existed solely to support the retired benchmark runner,
scenario fixtures, compatibility adapters, or the legacy dashboard.

The supported runtime path is:

`recoverit.cli investigate` → `recoverit.composition` → `investigation.graph`
→ configured live `collectors` → live reasoning provider.

## Removed

- Legacy state-machine orchestration in `investigation/orchestration/`.
- Benchmark runner, scenario evaluator, canned scenario data, and fixture
  adapters.
- Legacy collector interface/gateway stack and duplicate Git/log adapters.
- Cross-person schema translation adapters and the legacy alert-ingestion
  boundary used only by those adapters.
- Legacy dashboard, static assets, and CLI commands: `list`, `run`,
  `benchmark`, and `scan`.
- Tests that exercised only the deleted legacy behavior.

## Retained

- The LangGraph graph, graph nodes, routing, runtime composition, durable
  checkpointing, CLI `investigate`, and live HTTP API.
- Seven real read-only source adapters and their contract tests.
- Evidence processing, normalization, contracts, ranking, stopping rules, and
  live-API integration tests.
- Test-only fake and recorded reasoning providers. Their module headers now
  state their role explicitly; they are never selected by live composition.

## Guardrails

- Live services now require an explicit `ReasoningProvider`; they can no
  longer import a test double as an implicit fallback.
- All deletions are in Git on branch `cleanup`, so any individual path or the
  whole cleanup can be restored from its parent commit.
- Verification after the cleanup: `471 passed, 1 warning, 40 subtests passed`.
