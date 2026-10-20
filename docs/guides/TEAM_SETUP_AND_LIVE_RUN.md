# RecoverIT Team Setup and Live Run Guide

This guide is for teammates who are setting up RecoverIT on their own computer.
It assumes they do not already have the `cleanup` branch, the Dummy Project, or a
Gemini API key.

## 1. What this branch contains

The `cleanup` branch contains the current LangGraph-based orchestration and the
cleanup of the legacy orchestration stack. The intended flow is:

1. An incident seed is validated.
2. LangGraph initializes the investigation state.
3. The graph assesses evidence gaps and plans queries.
4. Source adapters collect evidence from configured real sources.
5. The graph builds context, generates hypotheses, and evaluates whether more
   evidence is required.
6. The graph ranks hypotheses and writes a Markdown report.

Gemini is used for reasoning over the structured evidence. It does not replace
the collectors: collectors must provide the actual repository, log, metrics, or
other source data.

The separate Dummy Project is not required. Each person should configure
RecoverIT against their own local application repository and logs.

## 2. One-time setup

The branch owner must push the branch before teammates can pull it:

```bash
git push -u origin cleanup
```

Then each teammate runs:

```bash
git clone <repository-url>
cd "Project I"
git fetch origin
git switch --track -c cleanup origin/cleanup
```

If the repository is already cloned:

```bash
cd "/absolute/path/to/Project I"
git fetch origin
git switch cleanup
git pull --ff-only origin cleanup
```

Create an isolated Python environment. Python 3.11 or newer is required.

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Verify the installation before attempting a live run:

```bash
python -m pytest -q
python -m recoverit.cli --help
```

All tests should pass. The current CLI intentionally exposes `investigate` and
`ui`; old commands such as `benchmark`, `scan`, or `run` are not part of the
cleaned-up interface.

## 3. Gemini API key

A Gemini key is required for a live investigation. No key is required for
installation, tests, or CLI validation.

Set the key only in the shell environment. Do not put it in a JSON file or
commit it to Git.

### macOS/Linux

```bash
export GEMINI_API_KEY="your-gemini-api-key"
```

### Windows PowerShell

```powershell
$env:GEMINI_API_KEY = "your-gemini-api-key"
```

The environment variable lasts only for the current terminal session. Obtain a
key through Google AI Studio using your own account. Never share the key in the
repository, chat, reports, screenshots, or logs.

If a teammate does not yet have a key, they can still run the tests and work on
collectors, contracts, and graph logic. The live `investigate` command will not
work without a configured key.

## 4. Configure a real local application

Create a local, untracked directory for personal runtime files:

```bash
mkdir -p runtime/logs
```

Do not commit this directory. It can contain logs, reports, checkpoints, and
other sensitive data. Add the following entries to `.gitignore` if they are not
already present:

```gitignore
.venv/
.recoverit/
runtime/
*.sqlite
.env
```

Use an actual application repository as `repo_path`, and its real log file as
`log_path`. Do not use a fixed JSON response as a substitute for collector
logic.

Create `runtime/runtime.gemini.json` and replace every example path with an
absolute path on the local machine:

```json
{
  "mode": "live",
  "checkpoint_database_path": "/absolute/path/to/your-service/.recoverit/checkpoints.sqlite",
  "llm_provider": "gemini",
  "llm_model": "gemini-3.5-flash-lite",
  "llm_timeout_seconds": 180,
  "llm_max_output_tokens": 4096,
  "max_concurrency": 2,
  "query_timeout_seconds": 20,
  "source_configs": [
    {
      "source_type": "changes",
      "implementation": "local_git",
      "enabled": true,
      "options": {
        "repo_path": "/absolute/path/to/your-service",
        "service_name": "your-service"
      }
    },
    {
      "source_type": "logs",
      "implementation": "file_log",
      "enabled": true,
      "options": {
        "log_path": "/absolute/path/to/your-service/runtime/logs/service.log",
        "service_name": "your-service",
        "only_errors": false,
        "timestamp_timezone": "UTC"
      }
    }
  ]
}
```

The supported live source implementations include `local_git`, `file_log`,
`prometheus`, `github_actions`, `kubernetes`, `git_configuration`, and
`http_health`. Enable only sources that are genuinely configured and available.
For a first run, `local_git` plus `file_log` is the smallest useful setup.

## 5. Create an incident seed

Create `runtime/incident.json`:

```json
{
  "incident_id": "inc-your-service-001",
  "external_alert_id": "alert-your-service-001",
  "service": "your-service",
  "environment": "local",
  "severity": "critical",
  "detected_at": "2026-10-10T12:00:00Z",
  "received_at": "2026-10-10T12:00:00Z",
  "summary": "HTTP 500 responses increased after a recent configuration change.",
  "labels": {
    "source": "manual"
  }
}
```

The timestamps must be valid ISO 8601 timestamps with timezone information.
Choose a time window that overlaps the actual log entries and recent repository
changes. The service name should match the service configured in the sources.

## 6. If there is no application or log file yet

For a pipeline smoke test only, create a small local log file:

```bash
mkdir -p runtime/logs
printf '%s\n' '2026-10-10T12:00:30Z ERROR [your-service] smoke test failure request_id=demo-001' > runtime/logs/service.log
```

Point `log_path` at that file and use an accessible Git repository for
`repo_path`. This verifies that the command can load configuration, collect a
real log file, run the graph, and produce a report. It is not a meaningful
root-cause investigation unless the repository and logs describe the same
application incident.

## 7. Run the investigation

With the virtual environment active and `GEMINI_API_KEY` set:

```bash
python -m recoverit.cli investigate \
  --config "/absolute/path/to/Project I/runtime/runtime.gemini.json" \
  --incident "/absolute/path/to/Project I/runtime/incident.json" \
  --report "/absolute/path/to/Project I/runtime/investigation-report.md"
```

If the environment is not activated, use the environment's Python directly:

```bash
.venv/bin/python -m recoverit.cli investigate \
  --config "/absolute/path/to/Project I/runtime/runtime.gemini.json" \
  --incident "/absolute/path/to/Project I/runtime/incident.json" \
  --report "/absolute/path/to/Project I/runtime/investigation-report.md"
```

On Windows, replace `.venv/bin/python` with `.venv\\Scripts\\python.exe`.

The trace should show initialization, gap assessment, query planning, evidence
collection, context updates, hypothesis updates, stopping evaluation, and
completion. A successful run should report `Status: COMPLETED`, identify the
Gemini provider/model, and save the Markdown report at the requested path.

## 8. Checkpoints and repeat runs

LangGraph persists checkpoints in the SQLite file configured by
`checkpoint_database_path`. This allows an investigation to resume, but it also
means that reusing the same incident ID can reuse prior state.

For a fresh run, either use a new `incident_id` and report path, or remove only
the configured checkpoint file after confirming its exact path:

```bash
rm -f "/absolute/path/to/your-service/.recoverit/checkpoints.sqlite"
```

Do not delete broad directories or unrelated project files.

## 9. Troubleshooting

### `No live LLM client is configured`

The key is missing from the current terminal, or the runtime is not in live
mode. Re-export `GEMINI_API_KEY` and check that the JSON contains
`"mode": "live"`, `"llm_provider": "gemini"`, and a valid model name.

### `ModuleNotFoundError`

Activate the correct virtual environment and rerun:

```bash
python -m pip install -e ".[dev]"
```

### No evidence is collected

Check that `repo_path` and `log_path` are absolute paths, exist, and are
readable. Check that the log timestamps overlap the incident time and that the
configured service name matches the incident service.

### The result is `INCONCLUSIVE`

This is a valid investigation result, not necessarily a software crash. It
usually means the evidence was insufficient, the source path or time window was
wrong, the incident budget ended, or the configured source was unavailable.
Inspect the report and correct the source configuration before rerunning.

### Gemini/API errors or timeouts

Confirm the key is valid, the model is available to the account, and the
machine has network access. Keep the initial configuration conservative and
increase timeouts only when the error indicates a genuine timeout.

When asking for help, share the complete terminal error, runtime configuration
with the API key removed, and relevant report section. Do not share the key or
unsanitized logs.

## 10. Working safely as a contributor

- Run `python -m pytest -q` before changing code and after completing a change.
- Keep personal keys, logs, reports, and SQLite checkpoints untracked.
- Preserve the existing dataflow and contracts unless the team explicitly
  agrees on a contract change.
- Add real collector behavior and tests; do not add fixed JSON outputs as
  production behavior.
- Keep changes focused and document any new schema, field, adapter, or graph
  state transition.
- Before the final merge, each person should provide the commit list, tests run,
  changed contracts, and any live-run evidence needed for integration.

