# RecoverIT

RecoverIT investigates CI/CD and production incidents using configured read-only sources. It builds evidence-linked hypotheses, ranks likely causes, and produces an investigation report with human-review remediation guidance.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

On Windows, activate the environment with `.venv\Scripts\activate` before installing.

Copy [docs/live-runtime.example.json](docs/live-runtime.example.json) and set the source configuration and provider credentials for your environment. Then run an investigation using an incident JSON such as [docs/incident.example.json](docs/incident.example.json):

```bash
python -m recoverit.cli investigate \
  --config path/to/runtime.json \
  --incident path/to/incident.json \
  --report path/to/investigation-report.md
```

To start the HTTP API and browser UI with the same configuration:

```bash
python -m recoverit.serve --config path/to/runtime.json
```

## Repository map

| Path | Purpose |
| --- | --- |
| `contracts/` | Shared incident, evidence, hypothesis, and result schemas |
| `collectors/` | Read-only source adapters |
| `investigation/` | LangGraph workflow, planning, and budgets |
| `evidence/`, `timeline/` | Context construction and evidence processing |
| `reasoning/`, `remediation/` | Hypothesis ranking and advisory plans |
| `recoverit/` | Runtime composition, CLI, API, and browser UI |
| `tests/` | Automated tests and fixtures |
| `docs/` | Architecture, guides, plans, phases, handoffs, and report outline |
| `Report/` | Academic report drafts, PDFs, images, and presentations |

Start with the [documentation index](docs/INDEX.md), [architecture](docs/architecture/ARCHITECTURE.md), and [repository guide](docs/guides/REPOSITORY_GUIDE.md). The [cleanup audit](docs/guides/CLEANUP_AUDIT.md) explains the current runtime path and retired components.
