# RecoverIT — Autonomous CI/CD Incident Investigator

An AI-driven DevOps agent that automatically investigates CI/CD and production incidents, identifies the most likely root cause using logs, metrics, deployment history, and code changes, and produces ranked root-cause hypotheses with supporting evidence.

## Project Setup

### Prerequisites

- Python 3.11+

### Install

```bash
# Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# Install the project with dev dependencies
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

## Run a configured investigation

The live runtime is configured explicitly; it does not load canned scenarios
or fixture sources.

```bash
python -m recoverit.cli investigate \
  --config path/to/runtime.json \
  --incident path/to/incident.json \
  --report path/to/investigation-report.md
```

## Repository Structure

```
contracts/          # Shared schemas — reviewed by all team members
  incident/         # IncidentAlert, IncidentSeed
  collection/       # SourceCapabilityCatalog, EvidenceQueryPlan, RawEvidenceBatch
  evidence/         # (Person 2) EvidenceRecord
  timeline/         # (Person 2) TimelineEvent, TemporalRelationship
  investigation/    # (Person 3) MissingInformationAssessment, InvestigationBudget
  hypothesis/       # (Person 3) Hypothesis, RankedHypothesisSet
  errors/           # StructuredError
recoverit/          # Live CLI, runtime composition, runner, and HTTP API
investigation/graph/# LangGraph nodes, routing, dependencies, and state
collectors/         # Real read-only source adapters and collection service
evidence/           # Evidence normalization and context construction
reasoning/          # LLM reasoning, hypothesis handling, ranking, stopping
tests/              # Shared test suites
docs/               # Architecture decisions and guides
```

See [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/CLEANUP_AUDIT.md](docs/CLEANUP_AUDIT.md) for details.
