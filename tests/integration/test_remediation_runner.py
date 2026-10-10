"""End-to-end live CLI/Runner and Presentation tests for remediation guidance."""

from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from contracts.enums import Severity
from contracts.errors.schemas import ProgressEvent
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from recoverit.cli import display_results
from recoverit.composition import RuntimeMode, RuntimeSettings, build_runtime
from recoverit.runner import InvestigationResult, InvestigationRunner
from recoverit.web.configured import create_configured_app
from tests.graph.test_investigation_graph_remediation import (
    MockRemediationPlan,
    MockRemediationStep,
    sample_dependencies,
    sample_input,
)


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class StaticCapabilityRegistry:
    def capabilities(self, incident):
        catalog = sample_input(incident.incident_id)["source_capabilities"]
        return catalog.model_copy(update={"incident_id": incident.incident_id})


@pytest.mark.asyncio
async def test_runner_produces_result_with_remediation_plan() -> None:
    expected_plan = MockRemediationPlan(
        plan_id="plan-runner-1",
        recommendation_available=True,
        risk="low",
        safety_notice="Advisory guidance only. Human approval required.",
    )
    deps, _, _ = sample_dependencies(remediation_plan=expected_plan)
    runtime = await build_runtime(
        RuntimeSettings(mode=RuntimeMode.TEST),
        registry=StaticCapabilityRegistry(),
        dependencies=deps,
    )
    runner = InvestigationRunner(runtime)

    incident = sample_input()["incident"]
    result = await runner.run(incident)

    assert result.remediation_plan is not None
    assert result.remediation_plan["plan_id"] == "plan-runner-1"
    assert result.remediation_plan["recommendation_available"] is True
    assert result.remediation_plan["risk"] == "low"
    assert result.remediation_plan["safety_notice"] == "Advisory guidance only. Human approval required."


def test_markdown_report_renders_available_recommendation() -> None:
    plan_dict = {
        "plan_id": "plan-avail",
        "incident_id": "inc-1",
        "created_at": NOW.isoformat(),
        "recommendation_available": True,
        "safety_notice": "Do not execute without human operator sign-off.",
        "hypothesis_id": "hyp-config-regression",
        "root_cause_category": "configuration",
        "confidence": "high",
        "evidence_ids": ["ev-1", "ev-2"],
        "risk": "medium",
        "prerequisites": ["Ensure admin console access", "Review change log"],
        "steps": [
            {
                "step_number": 1,
                "title": "Verify config revision",
                "purpose": "Confirm the baseline revision exists",
                "instructions": ["Open cluster config", "Locate commit SHA abcd123"],
                "expected_result": "Revision abcd123 is valid",
                "verification": ["Check Git log", "Confirm tag matches release"],
                "rollback_guidance": ["No rollback needed for read-only check"],
                "requires_human_approval": True,
            }
        ],
        "escalation_guidance": [],
        "unresolved_uncertainty": [],
    }
    result = InvestigationResult(
        incident_id="inc-1",
        service="payments",
        summary="Checkout failure rate increased",
        status="completed",
        stop_reason=None,
        execution_time_seconds=1.23,
        ranked_hypotheses=[],
        timeline_events=[],
        evidence_items=[],
        diff_excerpts={},
        log_excerpts=[],
        budget_usage={},
        provider_used="test-provider",
        remediation_plan=plan_dict,
    )

    report = result.to_markdown_report()

    assert "## Suggested Remediation — Human Review Required" in report
    assert "Do not execute without human operator sign-off." in report
    assert "**Operational Risk Tier:** `MEDIUM`" in report
    assert "Hypothesis: `hyp-config-regression`" in report
    assert "Category: `configuration`" in report
    assert "Confidence: `HIGH`" in report
    assert "Cited Evidence: `ev-1`, `ev-2`" in report
    assert "### Prerequisites" in report
    assert "Ensure admin console access" in report
    assert "#### Step 1: Verify config revision" in report
    assert "**Human Approval Required:** `Yes`" in report
    assert "Locate commit SHA abcd123" in report
    assert "Check Git log" in report


def test_markdown_report_renders_blocked_recommendation() -> None:
    plan_dict = {
        "plan_id": "plan-blocked",
        "incident_id": "inc-2",
        "created_at": NOW.isoformat(),
        "recommendation_available": False,
        "safety_notice": "Operator escalation required. Evidence insufficient.",
        "hypothesis_id": None,
        "root_cause_category": None,
        "confidence": None,
        "evidence_ids": [],
        "risk": "blocked",
        "prerequisites": [],
        "steps": [],
        "escalation_guidance": ["Contact database on-call engineer", "Verify replica synchronization"],
        "unresolved_uncertainty": ["Primary database node health unknown"],
    }
    result = InvestigationResult(
        incident_id="inc-2",
        service="database",
        summary="Replica timeout",
        status="inconclusive",
        stop_reason=None,
        execution_time_seconds=0.5,
        ranked_hypotheses=[],
        timeline_events=[],
        evidence_items=[],
        diff_excerpts={},
        log_excerpts=[],
        budget_usage={},
        provider_used="test-provider",
        remediation_plan=plan_dict,
    )

    report = result.to_markdown_report()

    assert "## Suggested Remediation — Human Review Required" in report
    assert "> **No production change recommended.**" in report
    assert "OPERATIONAL RISK TIER:** `BLOCKED`" in report.upper()
    assert "### Escalation Guidance" in report
    assert "Contact database on-call engineer" in report
    assert "### Unresolved Uncertainty" in report
    assert "Primary database node health unknown" in report


def test_cli_display_results_renders_remediation_panel() -> None:
    plan_dict = {
        "plan_id": "plan-cli",
        "incident_id": "inc-3",
        "created_at": NOW.isoformat(),
        "recommendation_available": True,
        "safety_notice": "Human operator review mandatory banner.",
        "hypothesis_id": "hyp-3",
        "root_cause_category": "deployment",
        "confidence": "high",
        "evidence_ids": ["ev-deploy-1"],
        "risk": "low",
        "prerequisites": ["Check cluster status"],
        "steps": [
            {
                "step_number": 1,
                "title": "Check pod status",
                "purpose": "Inspect failing containers",
                "instructions": ["Run kubectl get pods check"],
                "expected_result": "Pods healthy",
                "verification": ["Check health check"],
                "rollback_guidance": ["Revert deployment revision"],
                "requires_human_approval": True,
            }
        ],
        "escalation_guidance": [],
        "unresolved_uncertainty": [],
    }
    result = InvestigationResult(
        incident_id="inc-3",
        service="frontend",
        summary="502 Bad Gateway",
        status="completed",
        stop_reason=None,
        execution_time_seconds=0.8,
        ranked_hypotheses=[],
        timeline_events=[],
        evidence_items=[],
        diff_excerpts={},
        log_excerpts=[],
        budget_usage={},
        provider_used="test-provider",
        remediation_plan=plan_dict,
    )

    capture_console = Console(file=io.StringIO(), force_terminal=True, legacy_windows=False)
    # Monkey-patch console temporarily for test verification
    import recoverit.cli
    orig_console = recoverit.cli.console
    try:
        recoverit.cli.console = capture_console
        display_results(result)
        output = capture_console.file.getvalue()
        assert "Suggested Remediation — Human Review Required" in output
        assert "Human operator review mandatory banner" in output
        assert "OPERATIONAL RISK: LOW" in output
        assert "Step 1: Check pod status" in output
        assert "Check pod status" in output
    finally:
        recoverit.cli.console = orig_console


def test_web_configured_serves_remediation_and_static_assets(tmp_path) -> None:
    settings = RuntimeSettings(
        mode=RuntimeMode.TEST,
        checkpoint_database_path=tmp_path / "checkpoints.sqlite",
    )
    app = create_configured_app(settings)
    client = TestClient(app)

    # Test static assets
    resp_html = client.get("/static/index.html")
    assert resp_html.status_code == 200
    assert "Suggested Remediation — Human Review Required" in resp_html.text
    assert "remediation-section" in resp_html.text
    # Ensure no automated execution buttons exist in UI
    assert 'button id="execute"' not in resp_html.text.lower()
    assert 'button id="apply"' not in resp_html.text.lower()
    assert 'button id="rollback"' not in resp_html.text.lower()

    resp_css = client.get("/static/style.css")
    assert resp_css.status_code == 200
    assert "remediation-card" in resp_css.text
    assert "risk-blocked" in resp_css.text

    resp_js = client.get("/static/app.js")
    assert resp_js.status_code == 200
    assert "renderRemediationSection" in resp_js.text
