"""Freeze tests for contracts shared by the three migration workstreams."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import contracts.common as common
import contracts.enums as enums
from contracts.collection.schemas import (
    EvidenceQuery,
    EvidenceQueryPlan,
    SourceCapability,
    SourceCapabilityCatalog,
    SourceResult,
)
from contracts.errors.schemas import ProgressEvent, StructuredError
from contracts.hypothesis.schemas import BudgetUsage as ResultBudgetUsage
from contracts.investigation.schemas import (
    BudgetUsage,
    EvidenceQueryPlan as InvestigationQueryPlan,
    EvidenceQueryPlanQuery,
    StopDecision,
)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def test_shared_enums_have_one_definition_and_frozen_values() -> None:
    assert common.SourceType is enums.SourceType
    assert common.StopReason is enums.StopReason
    assert [source.value for source in enums.SourceType] == [
        "logs",
        "metrics",
        "changes",
        "deployments",
        "pipelines",
        "configuration",
        "health",
    ]
    assert enums.SourceCoverageStatus.PARTIAL == "partial"
    assert enums.InvestigationStatus.CANCELLED == "cancelled"


def test_query_plan_has_one_schema_and_accepts_legacy_input_name() -> None:
    assert InvestigationQueryPlan is EvidenceQueryPlan
    assert EvidenceQueryPlanQuery is EvidenceQuery

    plan = EvidenceQueryPlan(
        incident_id="inc-1",
        plan_id="plan-1",
        round=2,
        queries=[
            EvidenceQuery(
                query_id="query-1",
                source_type=enums.SourceType.LOGS,
                question="Which errors occurred?",
            )
        ],
    )

    assert plan.round_number == 2
    assert plan.round == 2
    assert "round_number" in plan.model_dump()
    assert "round" not in plan.model_dump()
    assert EvidenceQueryPlan.model_validate_json(plan.model_dump_json()) == plan


def test_capability_identity_is_required_and_source_types_are_unique() -> None:
    with pytest.raises(ValidationError, match="adapter_name"):
        SourceCapability(
            source_type=enums.SourceType.LOGS,
            available=True,
        )

    capability = SourceCapability(
        source_type=enums.SourceType.LOGS,
        available=True,
        adapter_name="file-logs",
    )
    with pytest.raises(ValidationError, match="at most one capability"):
        SourceCapabilityCatalog(
            incident_id="inc-1",
            generated_at=NOW,
            sources=[capability, capability],
        )


def test_source_result_requires_an_execution_window() -> None:
    values = {
        "query_id": "query-1",
        "source_type": enums.SourceType.LOGS,
        "source_adapter": "file-logs",
        "source_status": enums.SourceStatus.EMPTY,
    }
    with pytest.raises(ValidationError, match="started_at"):
        SourceResult(**values)

    result = SourceResult(
        **values,
        started_at=NOW,
        completed_at=NOW,
    )
    assert SourceResult.model_validate_json(result.model_dump_json()) == result


def test_errors_progress_and_decisions_round_trip_without_legacy_fields() -> None:
    error = StructuredError(
        code="SOURCE_TIMEOUT",
        message="Log query timed out.",
        stage="collect_evidence",
        retryable=True,
        source_type=enums.SourceType.LOGS,
        details={"query_id": "query-1"},
    )
    assert StructuredError.model_validate_json(error.model_dump_json()) == error
    with pytest.raises(ValidationError, match="stage"):
        StructuredError(code="SOURCE_TIMEOUT", message="Timed out")
    with pytest.raises(ValidationError, match="Extra inputs"):
        StructuredError(
            code="SOURCE_TIMEOUT",
            message="Timed out",
            stage="collect_evidence",
            source="logs",
        )

    event = ProgressEvent(
        event_id="event-1",
        incident_id="inc-1",
        kind="status",
        stage="initialize",
        title="Investigation initialized",
        created_at=NOW,
    )
    assert ProgressEvent.model_validate_json(event.model_dump_json()) == event

    decision = StopDecision(
        action=enums.StopAction.RANK,
        reason="Sufficient independent evidence.",
        stop_reason=enums.StopReason.SUFFICIENT_EVIDENCE,
        criteria_status={"independent_sources": True},
    )
    assert StopDecision.model_validate_json(decision.model_dump_json()) == decision


def test_budget_usage_is_shared_by_investigation_and_final_result() -> None:
    assert ResultBudgetUsage is BudgetUsage
    usage = BudgetUsage(rounds=1, queries=2, reasoning_calls=3)
    assert BudgetUsage.model_validate_json(usage.model_dump_json()) == usage
