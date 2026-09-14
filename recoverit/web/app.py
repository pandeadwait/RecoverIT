"""FastAPI backend application for the RecoverIT Incident Cockpit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from collectors.fixtures import (
    canonical_scenario_id,
    list_available_scenarios,
    load_scenario_json,
    resolve_scenario_name,
)
from recoverit.runner import InvestigationRunner

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="RecoverIT Dashboard API",
    description="Autonomous CI/CD Incident Triager & Self-Healer Web Cockpit",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runner = InvestigationRunner()

SCENARIO_METADATA = {
    "incident_001": {
        "title": "[P1 CRITICAL] HTTP 500 Spike on payment-api (>35% failure rate)",
        "service": "payment-api",
        "category": "High Error Rate Alert",
        "severity": "CRITICAL",
        "description": "Alert triggered by Prometheus: payment-api error rate exceeded 30% threshold over a 5-minute evaluation window.",
    },
    "incident_002": {
        "title": "[P1 CRITICAL] Worker Pod CrashLoopBackOff on order-api (Exit Code 137)",
        "service": "order-api",
        "category": "Container Eviction Alert",
        "severity": "CRITICAL",
        "description": "Alert triggered by Kubernetes: 4 worker pods terminated unexpectedly with exit code 137 (OOMKilled) in production.",
    },
    "incident_003": {
        "title": "[P2 HIGH] Service Startup Failure on auth-service after build",
        "service": "auth-service",
        "category": "Deployment Crash Alert",
        "severity": "HIGH",
        "description": "Alert triggered by CI/CD pipeline: auth-service canary instance failing container health checks on port 8080.",
    },
    "incident_004": {
        "title": "[P1 CRITICAL] Database Connection Pool Exhaustion on billing-api",
        "service": "billing-api",
        "category": "Infrastructure Outage Alert",
        "severity": "CRITICAL",
        "description": "Alert triggered by Datadog: 0 of 50 PostgreSQL connections available across billing service instances.",
    },
    "incident_005": {
        "title": "[P1 CRITICAL] Checkout Failure Rate Surge on checkout-api",
        "service": "checkout-api",
        "category": "Transaction Failure Alert",
        "severity": "CRITICAL",
        "description": "Alert triggered by PagerDuty: Checkout transaction failure rate surged to 42% at 10:27 UTC.",
    },
}

# Alias backwards compatibility
SCENARIO_METADATA["bad_db_config"] = SCENARIO_METADATA["incident_001"]
SCENARIO_METADATA["memory_exhaustion"] = SCENARIO_METADATA["incident_002"]
SCENARIO_METADATA["dependency_incompatibility"] = SCENARIO_METADATA["incident_003"]
SCENARIO_METADATA["real_db_outage"] = SCENARIO_METADATA["incident_004"]
SCENARIO_METADATA["coincidental_deployment"] = SCENARIO_METADATA["incident_005"]


class InvestigationRequest(BaseModel):
    scenario: str = Field(default="incident_001")
    mode: str = Field(default="auto")
    provider: str = Field(default="auto")
    model: str | None = Field(default=None)
    repo_path: str | None = Field(default=None)
    log_path: str | None = Field(default=None)
    service_name: str | None = Field(default=None)
    summary: str | None = Field(default=None)


@app.get("/api/scenarios")
async def get_scenarios() -> list[dict[str, Any]]:
    """Return available incident benchmark scenarios."""
    scenarios = []
    for sid in list_available_scenarios(only_canonical=True):
        alias = resolve_scenario_name(sid)
        meta = SCENARIO_METADATA.get(sid, {})
        data = load_scenario_json(sid)
        scenarios.append({
            "id": sid,
            "alias": alias,
            "title": meta.get("title", data.get("title", sid)),
            "service": meta.get("service", data.get("service", "unknown")),
            "category": meta.get("category", "General"),
            "severity": meta.get("severity", "CRITICAL"),
            "description": meta.get("description", ""),
        })
    return scenarios


@app.post("/api/investigate")
async def investigate_incident(req: InvestigationRequest) -> dict[str, Any]:
    """Execute autonomous incident investigation and return diagnosis."""
    try:
        if req.repo_path:
            result = await runner.run_target(
                repo_path=req.repo_path,
                log_path=req.log_path,
                service_name=req.service_name or "target-service",
                summary=req.summary or "[P1 CRITICAL] Operational degradation detected",
                mode=req.mode,
                provider=req.provider,
                llm_model=req.model,
            )
        else:
            result = await runner.run_scenario(
                scenario_name=req.scenario,
                mode=req.mode,
                provider=req.provider,
                llm_model=req.model,
            )

        return {
            "incident_id": result.incident_id,
            "service": result.service,
            "summary": result.summary,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "execution_time_seconds": result.execution_time_seconds,
            "provider_used": result.provider_used,
            "ranked_hypotheses": result.ranked_hypotheses,
            "timeline_events": result.timeline_events,
            "evidence_items": result.evidence_items,
            "diff_excerpts": result.diff_excerpts,
            "budget_usage": result.budget_usage,
            "markdown_report": result.to_markdown_report(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/report/{scenario}")
async def get_report(scenario: str) -> PlainTextResponse:
    """Download Markdown incident post-mortem for a scenario."""
    try:
        result = await runner.run_scenario(scenario_name=scenario, mode="offline")
        return PlainTextResponse(result.to_markdown_report(), media_type="text/markdown")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# Serve static web frontend
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return PlainTextResponse("RecoverIT Web Dashboard frontend is initializing...", status_code=200)
    return FileResponse(index_file)
