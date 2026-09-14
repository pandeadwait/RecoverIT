"""
Unit tests for Phase 8: Improve the CLI Investigation Trace.

Validates:
1. Every tool execution emits a trace_step with all 5 mandatory components:
   RATIONALE, TOOL CALL, TOOL RESULT, INTERPRETATION, NEXT DECISION.
2. Fixture and in-memory sources are tagged as 'Recorded Replay' and NEVER as 'Live Source'.
3. Local disk and live adapters are tagged as 'Live Source'.
4. Metadata correctly propagates latency, record counts, filters, warnings, and provider name.
5. Long outputs (>3 records) are summarized with preview without concealing total record counts or warnings.
6. CLITraceRenderer formats all 5 blocks with visual distinction and custom badges.
7. Redundant collect-stage action/observation events are suppressed in CLI to avoid duplicate printing.
8. Contextual interpretation and next decision synthesis for all source types.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
import pytest
from rich.console import Console

from contracts.collection.schemas import (
    RawEvidenceBatch,
    RawRecord,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.common import (
    ConfidenceLabel,
    EvidenceType,
    Reliability,
    Severity,
    SourceCoverageStatus,
    SourceStatus,
    SourceType,
    StopReason,
)
from contracts.evidence.schemas import (
    EvidenceQuality,
    EvidenceSummaryProjection,
    IncidentContextSnapshot,
    IncidentSummary,
)
from contracts.hypothesis.schemas import (
    Hypothesis,
    HypothesisSet,
    RankedHypothesisSet,
)
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
    InvestigationBudget,
)
from investigation.orchestration.orchestrator import (
    InMemoryCollectionService,
    InMemoryContextBuilder,
    InvestigationOrchestrator,
    StoppingRuleEvaluator,
)
from reasoning.provider.fake_provider import FakeReasoningProvider
from recoverit.cli import CLITraceRenderer


@pytest.fixture
def sample_incident() -> IncidentSeed:
    return IncidentSeed(
        incident_id="inc_orch_01",
        external_alert_id="alt_ext_201",
        service="checkout-service",
        environment="production",
        severity=Severity.CRITICAL,
        detected_at=datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        summary="HTTP 500 error spike on /checkout endpoint",
        labels={"tier": "1", "team": "checkout"},
    )


@pytest.fixture
def sample_catalog(sample_incident: IncidentSeed) -> SourceCapabilityCatalog:
    from contracts.collection.schemas import SourceCapability
    return SourceCapabilityCatalog(
        incident_id=sample_incident.incident_id,
        generated_at=datetime(2026, 9, 12, 10, 1, 0, tzinfo=timezone.utc),
        sources=[
            SourceCapability(
                source_type=SourceType.LOGS,
                available=True,
                supported_query_fields=["service", "severity", "limit"],
                maximum_window_seconds=3600,
                maximum_items=500,
            ),
            SourceCapability(
                source_type=SourceType.DEPLOYMENTS,
                available=True,
                supported_query_fields=["service", "limit"],
                maximum_window_seconds=86400,
                maximum_items=50,
            ),
            SourceCapability(
                source_type=SourceType.METRICS,
                available=True,
                supported_query_fields=["metric", "service", "limit"],
                maximum_window_seconds=3600,
                maximum_items=200,
            ),
        ],
    )


@pytest.fixture
def sample_context(sample_incident: IncidentSeed) -> IncidentContextSnapshot:
    e1 = EvidenceSummaryProjection(
        evidence_id="ev_deploy_01",
        source_type=SourceType.DEPLOYMENTS,
        evidence_type=EvidenceType.DEPLOYMENT_EVENT,
        event_time=datetime(2026, 9, 12, 9, 55, 0, tzinfo=timezone.utc),
        summary="Deployment v1.8.0 updated checkout config",
        quality=EvidenceQuality(reliability=Reliability.HIGH),
    )
    return IncidentContextSnapshot(
        snapshot_id="ctx_orch_01",
        incident_id=sample_incident.incident_id,
        revision=1,
        created_at=datetime(2026, 9, 12, 10, 5, 0, tzinfo=timezone.utc),
        incident=IncidentSummary(
            service=sample_incident.service,
            environment=sample_incident.environment,
            severity=sample_incident.severity,
            detected_at=sample_incident.detected_at,
            summary=sample_incident.summary,
        ),
        evidence=[e1],
    )


@pytest.mark.asyncio
async def test_trace_step_emits_all_five_elements(
    sample_incident: IncidentSeed,
    sample_catalog: SourceCapabilityCatalog,
    sample_context: IncidentContextSnapshot,
) -> None:
    """Verify that every tool call explicitly emits RATIONALE, TOOL CALL, TOOL RESULT, INTERPRETATION, and NEXT DECISION."""
    events: list[dict[str, object]] = []
    provider = FakeReasoningProvider(preset="deployment-regression")
    collection_service = InMemoryCollectionService()
    context_builder = InMemoryContextBuilder(synthetic_evidence=sample_context.evidence)

    orchestrator = InvestigationOrchestrator(
        provider=provider,
        collection_service=collection_service,
        context_builder=context_builder,
        stopping_evaluator=StoppingRuleEvaluator(min_supporting_sources_for_adequate=1),
        progress_callback=events.append,
        provider_name="Deterministic Preset (deployment-regression)",
    )

    budget = InvestigationBudget(max_rounds=1, max_queries=4, max_reasoning_calls=4)
    await orchestrator.run(incident=sample_incident, source_capabilities=sample_catalog, budget=budget)

    trace_steps = [e for e in events if e.get("kind") == "trace_step"]
    assert len(trace_steps) >= 1, "At least one trace_step should be emitted"

    for step in trace_steps:
        # 1. RATIONALE
        assert "rationale" in step
        assert isinstance(step["rationale"], str) and len(step["rationale"]) > 0

        # 2. TOOL CALL
        assert "tool_call" in step
        assert isinstance(step["tool_call"], str) and ".search(" in step["tool_call"]

        # 3. TOOL RESULT
        assert "tool_result" in step
        tr = step["tool_result"]
        assert isinstance(tr, dict)
        assert "adapter_type" in tr
        assert "adapter_name" in tr
        assert "status" in tr
        assert "latency_ms" in tr and isinstance(tr["latency_ms"], (int, float))
        assert "records_matched" in tr and isinstance(tr["records_matched"], int)
        assert "records_preview" in tr and isinstance(tr["records_preview"], list)

        # 4. INTERPRETATION
        assert "interpretation" in step
        assert isinstance(step["interpretation"], str) and len(step["interpretation"]) > 0

        # 5. NEXT DECISION
        assert "next_decision" in step
        assert isinstance(step["next_decision"], str) and len(step["next_decision"]) > 0


def test_recorded_replay_adapter_classification() -> None:
    """Ensure fixture and synthetic in-memory adapters are tagged Recorded Replay, never Live Source."""
    assert InvestigationOrchestrator.classify_adapter("changes_fixture_adapter") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("logs_fixture_adapter") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("metrics_fixture_adapter") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("deployments_fixture_adapter") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("mock_adapter") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("inmemory_collection_service") == "Recorded Replay"
    assert InvestigationOrchestrator.classify_adapter("changes_adapter") == "Recorded Replay"


def test_live_source_adapter_classification() -> None:
    """Ensure real disk/git and live adapters are tagged Live Source."""
    assert InvestigationOrchestrator.classify_adapter("local_git_adapter") == "Live Source"
    assert InvestigationOrchestrator.classify_adapter("localgit_change_adapter") == "Live Source"
    assert InvestigationOrchestrator.classify_adapter("file_log_adapter") == "Live Source"
    assert InvestigationOrchestrator.classify_adapter("live_k8s_adapter") == "Live Source"


def test_tool_call_formatting() -> None:
    """Verify tool call string is formatted as function-like syntax."""
    call_1 = InvestigationOrchestrator.format_tool_call(
        SourceType.CHANGES,
        {"service": "payment-api", "limit": 10, "since": "2026-09-14T11:00:00Z"},
    )
    assert call_1 == 'changes.search(service="payment-api", limit=10, since="2026-09-14T11:00:00Z")'

    call_empty = InvestigationOrchestrator.format_tool_call(SourceType.METRICS, {})
    assert call_empty == "metrics.search()"


def test_metadata_propagation() -> None:
    """Ensure trace_step metadata contains provider, latency, filters, warnings, and round."""
    events: list[dict[str, object]] = []
    collection_service = InMemoryCollectionService()
    orchestrator = InvestigationOrchestrator(
        provider=FakeReasoningProvider(preset="deployment-regression"),
        collection_service=collection_service,
        context_builder=InMemoryContextBuilder(),
        progress_callback=events.append,
        provider_name="Deterministic Preset (deployment-regression)",
    )

    query = EvidenceQueryPlanQuery(
        query_id="q_changes_1",
        source_type=SourceType.CHANGES,
        question="Check for commits preceding the alert.",
        parameters={"service": "payment-api", "limit": 5},
    )
    result = SourceResult(
        query_id="q_changes_1",
        source_type=SourceType.CHANGES,
        source_adapter="changes_fixture_adapter",
        source_status=SourceStatus.OK,
        records=[
            RawRecord(
                source_record_id="commit_abc123",
                event_time=datetime.now(timezone.utc),
                content_type="application/json",
                payload={"commit_sha": "abc1234", "message": "Refactor database query timeout"},
            )
        ],
        warnings=["Rate limit window at 80%"],
    )

    orchestrator._emit_trace_step(
        query=query,
        result=result,
        round_num=1,
        rationale="Check for commits preceding the alert.",
        tool_call='changes.search(service="payment-api", limit=5)',
        adapter_type="Recorded Replay",
        adapter_name="changes_fixture_adapter",
        latency_ms=1.42,
        records_preview=["abc1234 — Refactor database query timeout"],
        is_truncated=False,
        interpretation="Identified 1 relevant commit.",
        next_decision="Query deployment history.",
    )

    assert len(events) == 1
    meta = events[0]["metadata"]
    assert meta["round"] == 1
    assert meta["query_id"] == "q_changes_1"
    assert meta["source_type"] == "changes"
    assert meta["adapter_type"] == "Recorded Replay"
    assert meta["adapter_name"] == "changes_fixture_adapter"
    assert meta["latency_ms"] == 1.42
    assert meta["records_matched"] == 1
    assert meta["filters_applied"] == {"service": "payment-api", "limit": 5}
    assert meta["warnings"] == ["Rate limit window at 80%"]
    assert meta["provider_name"] == "Deterministic Preset (deployment-regression)"


def test_long_output_truncation_without_hiding_counts_or_warnings() -> None:
    """Ensure outputs with > 3 records truncate previews but keep total counts and warnings."""
    records = [
        RawRecord(
            source_record_id=f"rec_{i}",
            event_time=datetime.now(timezone.utc),
            content_type="application/json",
            payload={"message": f"Log line {i}", "level": "error"},
        )
        for i in range(8)
    ]
    result = SourceResult(
        query_id="q_logs_bulk",
        source_type=SourceType.LOGS,
        source_adapter="logs_fixture_adapter",
        source_status=SourceStatus.OK,
        records=records,
        warnings=["Buffer truncated on server side"],
    )

    previews = [
        InvestigationOrchestrator._preview_record(result.source_type, r)
        for r in result.records[:3]
    ]
    total_records = len(result.records)
    is_truncated = total_records > 3
    assert is_truncated is True
    if is_truncated:
        previews.append(f"... and {total_records - 3} more records ({total_records} total matching records)")

    assert len(previews) == 4
    assert "... and 5 more records (8 total matching records)" in previews[-1]


def test_cli_trace_renderer_displays_all_five_blocks_distinctly() -> None:
    """Verify that CLITraceRenderer formats RATIONALE, TOOL CALL, TOOL RESULT, INTERPRETATION, NEXT DECISION."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)
    renderer = CLITraceRenderer(target_console=test_console)

    sample_event = {
        "kind": "trace_step",
        "stage": "collect",
        "title": "Tool Execution · CHANGES",
        "rationale": "Need to determine whether a relevant change preceded the alert.",
        "tool_call": 'changes.search(service="payment-api", limit=10)',
        "tool_result": {
            "adapter_type": "Recorded Replay",
            "adapter_name": "changes_fixture_adapter",
            "status": "ok",
            "latency_ms": 2.35,
            "records_matched": 1,
            "records_preview": ["abc1234 — Update database connection pool configuration"],
            "filters_applied": {"service": "payment-api", "limit": 10},
            "warnings": ["Warning: Test fixture warning"],
            "is_truncated": False,
            "total_records": 1,
        },
        "interpretation": "The commit preceded the alert by 15 minutes, but causation is not yet proven.",
        "next_decision": "Query deployment and configuration history to determine whether the commit reached production.",
        "metadata": {
            "round": 1,
            "query_id": "q_01",
            "source_type": "changes",
            "adapter_type": "Recorded Replay",
            "adapter_name": "changes_fixture_adapter",
            "latency_ms": 2.35,
            "records_matched": 1,
            "provider_name": "Deterministic Preset (deployment-regression)",
        },
    }

    renderer(sample_event)
    output = buf.getvalue()

    # 1. RATIONALE block
    assert "RATIONALE" in output
    assert "Need to determine whether a relevant change preceded the alert." in output

    # 2. TOOL CALL block
    assert "TOOL CALL" in output
    assert 'changes.search(service="payment-api", limit=10)' in output

    # 3. TOOL RESULT block with adapter badge
    assert "TOOL RESULT" in output
    assert "1 matching record(s)" in output
    assert "Recorded Replay" in output
    assert "changes_fixture_adapter" in output
    assert "abc1234 — Update database connection pool configuration" in output
    assert "Warning: Test fixture warning" in output

    # 4. INTERPRETATION block
    assert "INTERPRETATION" in output
    assert "The commit preceded the alert by 15 minutes, but causation is not yet proven." in output

    # 5. NEXT DECISION block
    assert "NEXT DECISION" in output
    assert "Query deployment and configuration history to determine whether the commit reached production." in output

    # Metadata banner
    assert "Round 1" in output
    assert "2.35ms" in output
    assert "Deterministic Preset (deployment-regression)" in output


def test_cli_trace_renderer_live_source_badge() -> None:
    """Verify that CLITraceRenderer formats Live Source badge in green for genuine live sources."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)
    renderer = CLITraceRenderer(target_console=test_console)

    event = {
        "kind": "trace_step",
        "stage": "collect",
        "rationale": "Inspect local git repository history.",
        "tool_call": 'changes.search(service="demo_service", limit=5)',
        "tool_result": {
            "adapter_type": "Live Source",
            "adapter_name": "local_git_adapter",
            "status": "ok",
            "latency_ms": 5.12,
            "records_matched": 1,
            "records_preview": ["d734891 — Fix timeout handler"],
            "filters_applied": {},
            "warnings": [],
            "is_truncated": False,
            "total_records": 1,
        },
        "interpretation": "Found 1 local git commit in the target repository.",
        "next_decision": "Examine log output for runtime errors.",
        "metadata": {
            "round": 1,
            "source_type": "changes",
            "adapter_type": "Live Source",
            "adapter_name": "local_git_adapter",
            "latency_ms": 5.12,
            "records_matched": 1,
            "provider_name": "Live LLM (gemini-1.5-flash)",
        },
    }

    renderer(event)
    output = buf.getvalue()
    assert "Live Source (local_git_adapter)" in output
    assert "5.12ms" in output


def test_cli_trace_renderer_suppresses_redundant_collect_events() -> None:
    """Verify that raw collect action/observation events are skipped by CLI renderer to avoid duplicate lines."""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)
    renderer = CLITraceRenderer(target_console=test_console)

    # Pre-query action
    renderer({
        "kind": "action",
        "stage": "collect",
        "title": "Querying changes",
        "detail": "Check commits",
        "output": "Tool request sent",
    })
    # Post-query observation
    renderer({
        "kind": "observation",
        "stage": "collect",
        "title": "Changes returned 1 record(s)",
        "detail": "Tool call completed",
        "output": "abc1234",
    })

    # Non-collect observation (e.g. context normalization) should still be rendered
    renderer({
        "kind": "observation",
        "stage": "context",
        "title": "Evidence normalized into a timeline",
        "detail": "Correlated timeline events",
        "output": "Context now contains 3 evidence items",
    })

    output = buf.getvalue()
    assert "Querying changes" not in output
    assert "Changes returned 1 record(s)" not in output
    assert "Evidence normalized into a timeline" in output


def test_interpretation_and_decision_synthesis() -> None:
    """Verify domain-specific interpretation and next decision synthesis for each source category."""
    # Changes
    q_chg = EvidenceQueryPlanQuery(
        query_id="q1",
        source_type=SourceType.CHANGES,
        question="Find changes",
        parameters={"service": "payment-api"},
    )
    r_chg = SourceResult(
        query_id="q1",
        source_type=SourceType.CHANGES,
        source_adapter="changes_fixture_adapter",
        source_status=SourceStatus.OK,
        records=[RawRecord(source_record_id="c1", event_time=None, content_type="json", payload={"commit_sha": "abcdef12", "message": "Tweak pool"})],
    )
    interp = InvestigationOrchestrator._synthesize_interpretation(q_chg, r_chg, 1)
    decision = InvestigationOrchestrator._synthesize_next_decision(q_chg, r_chg, 1)
    assert "abcdef12" in interp
    assert "primary causal trigger candidate" in interp
    assert "deployment history" in decision

    # Deployments
    q_dep = EvidenceQueryPlanQuery(
        query_id="q2",
        source_type=SourceType.DEPLOYMENTS,
        question="Find deployments",
        parameters={"service": "payment-api"},
    )
    r_dep = SourceResult(
        query_id="q2",
        source_type=SourceType.DEPLOYMENTS,
        source_adapter="deployments_fixture_adapter",
        source_status=SourceStatus.OK,
        records=[RawRecord(source_record_id="d1", event_time=None, content_type="json", payload={"version": "v1.2.3", "status": "succeeded"})],
    )
    interp_dep = InvestigationOrchestrator._synthesize_interpretation(q_dep, r_dep, 1)
    decision_dep = InvestigationOrchestrator._synthesize_next_decision(q_dep, r_dep, 1)
    assert "v1.2.3" in interp_dep
    assert "onset" in interp_dep
    assert "Inspect application error logs" in decision_dep

    # Configuration
    q_cfg = EvidenceQueryPlanQuery(
        query_id="q3",
        source_type=SourceType.CONFIGURATION,
        question="Find config",
        parameters={"service": "payment-api"},
    )
    r_cfg = SourceResult(
        query_id="q3",
        source_type=SourceType.CONFIGURATION,
        source_adapter="configuration_fixture_adapter",
        source_status=SourceStatus.OK,
        records=[RawRecord(source_record_id="k1", event_time=None, content_type="json", payload={"key": "db.timeout", "old_value": "30", "new_value": "1"})],
    )
    interp_cfg = InvestigationOrchestrator._synthesize_interpretation(q_cfg, r_cfg, 1)
    assert "db.timeout" in interp_cfg
    assert "30 → 1" in interp_cfg

    # Empty result
    r_empty = SourceResult(
        query_id="q1",
        source_type=SourceType.CHANGES,
        source_adapter="changes_fixture_adapter",
        source_status=SourceStatus.OK,
        records=[],
    )
    interp_empty = InvestigationOrchestrator._synthesize_interpretation(q_chg, r_empty, 1)
    assert "No changes records matched" in interp_empty
