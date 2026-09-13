# RecoverIT — Autonomous CI/CD Incident Triager & Self-Healer

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
ingestion/          # Person 1 — alert intake and validation
collectors/         # Person 1 — source adapters and collection service
tests/              # Shared test suites
docs/               # Architecture decisions and guides
```

See [ARCHITECTURE.md](ARCHITECTURE.md) and [WORK_DIVISION.md](WORK_DIVISION.md) for full details.
