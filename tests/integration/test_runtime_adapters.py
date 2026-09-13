"""Regression coverage for cross-person contracts and production adapters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from collectors.fixtures import create_scenario_adapters, load_scenario_json
from collectors.gateway.collection_service import DefaultCollectionService
from collectors.logs.fixture_adapter import FixtureLogAdapter
from contracts.collection import RawEvidenceBatch as Person2RawEvidenceBatch
from contracts.collection.query_plan import EvidenceQueryPlan as Person1EvidenceQueryPlan
from contracts.common import ContractValidationError
from contracts.incident import IncidentSeed as Person2IncidentSeed
from contracts.incident.seed import IncidentSeed as Person1IncidentSeed
from contracts.incident.schemas import IncidentSeed as Person3IncidentSeed
from contracts.investigation.schemas import (
    EvidenceQueryPlan as Person3EvidenceQueryPlan,
    EvidenceQueryPlanQuery,
)
from ingestion.capabilities.registry import DefaultSourceRegistry
from integration_runtime import Person1CollectionAdapter, Person2ContextAdapter
from contracts.enums import SourceStatus


SCENARIOS = (
    "bad_db_config",
    "memory_exhaustion",
    "dependency_incompatibility",
    "real_db_outage",
    "coincidental_deployment",
)
SOURCE_TYPES = (
    "logs",
    "metrics",
    "changes",
    "deployments",
    "pipelines",
    "configuration",
)


def _incident(scenario: str) -> Person3IncidentSeed:
    data = load_scenario_json(scenario)
    return Person3IncidentSeed(
        incident_id=f"inc-{scenario}",
        external_alert_id=f"alert-{scenario}",
        service=data["service"],
        environment="simulation",
        severity="critical",
        detected_at=data["detected_at"],
        received_at=data["detected_at"],
        summary=data["title"],
        labels={},
    )


def _collector(scenario: str) -> Person1CollectionAdapter:
    registry = DefaultSourceRegistry()
    for source in create_scenario_adapters(scenario).values():
        registry.register_source(source)
    return Person1CollectionAdapter(DefaultCollectionService(registry=registry))


def _plan(incident_id: str, round_number: int = 1) -> Person3EvidenceQueryPlan:
    return Person3EvidenceQueryPlan(
        incident_id=incident_id,
        plan_id=f"plan-{round_number}",
        round=round_number,
        queries=[
            EvidenceQueryPlanQuery(
                query_id=f"query-{round_number}-{source_type}",
                source_type=source_type,
                question=f"Collect {source_type} evidence",
                parameters={},
                expected_information_value="high",
            )
            for source_type in SOURCE_TYPES
        ],
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.asyncio
async def test_real_layers_handoff_all_scenarios(scenario: str) -> None:
    incident = _incident(scenario)
    batch = await _collector(scenario).execute(_plan(incident.incident_id))
    context = await Person2ContextAdapter(incident).build(incident.incident_id, batch)

    assert len(batch.results) == len(SOURCE_TYPES)
    assert all(result.source_status == "ok" for result in batch.results)
    assert context.incident_id == incident.incident_id
    assert context.revision == 1
    assert len(context.evidence) == sum(len(result.records) for result in batch.results)
    assert set(context.source_coverage.values()) == {"available"}
    assert {item.evidence_id for item in context.evidence} == {
        evidence_id for event in context.timeline for evidence_id in event.evidence_ids
    }


@pytest.mark.asyncio
async def test_context_adapter_advances_revision_and_accepts_previous_projection() -> None:
    incident = _incident("bad_db_config")
    collector = _collector("bad_db_config")
    builder = Person2ContextAdapter(incident)

    first_batch = await collector.execute(_plan(incident.incident_id, 1))
    first = await builder.build(incident.incident_id, first_batch)
    second_batch = await collector.execute(_plan(incident.incident_id, 2))
    second = await builder.build(incident.incident_id, second_batch, first)

    assert second.revision == 2
    assert len(second.evidence) == len(first.evidence)


@pytest.mark.asyncio
async def test_timeout_batch_crosses_person1_to_person2_boundary() -> None:
    incident = _incident("bad_db_config")
    registry = DefaultSourceRegistry()
    registry.register_source(
        FixtureLogAdapter(
            scenario_name="bad_db_config",
            simulated_status=SourceStatus.TIMEOUT,
        )
    )
    collector = Person1CollectionAdapter(DefaultCollectionService(registry=registry))
    plan = Person3EvidenceQueryPlan(
        incident_id=incident.incident_id,
        plan_id="timeout-plan",
        round=1,
        queries=[
            EvidenceQueryPlanQuery(
                query_id="timeout-query",
                source_type="logs",
                question="Collect logs",
                parameters={},
                expected_information_value="high",
            )
        ],
    )

    batch = await collector.execute(plan)
    context = await Person2ContextAdapter(incident).build(incident.incident_id, batch)

    assert batch.results[0].source_status == "timeout"
    assert batch.errors[0]["source"] == "logs"
    assert context.source_coverage["logs"] == "unavailable"


def test_person1_partial_batch_is_accepted_by_person2_without_losing_errors() -> None:
    payload = {
        "schema_version": "1.0",
        "incident_id": "inc-1",
        "plan_id": "plan-1",
        "batch_id": "batch-1",
        "collected_at": "2026-09-12T10:31:00Z",
        "results": [
            {
                "query_id": "query-1",
                "source_type": "logs",
                "source_adapter": "fixture-log",
                "source_status": "partial",
                "truncated": True,
                "records": [],
                "warnings": ["source returned a partial page"],
            }
        ],
        "errors": [
            {
                "schema_version": "1.0",
                "code": "SOURCE_TIMEOUT",
                "message": "page two timed out",
                "retryable": True,
                "source": "logs",
                "details": {"query_id": "query-1"},
            }
        ],
    }

    batch = Person2RawEvidenceBatch.from_dict(payload)

    assert batch.results[0].warnings[0].message == "source returned a partial page"
    assert batch.errors[0].code == "SOURCE_TIMEOUT"
    assert batch.errors[0].source == "logs"


def test_all_contract_boundaries_reject_unknown_fields_and_versions() -> None:
    valid = _incident("bad_db_config").model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Person3IncidentSeed.model_validate({**valid, "future_field": True})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Person1IncidentSeed.model_validate({**valid, "future_field": True})
    with pytest.raises(ContractValidationError, match="unknown_field"):
        Person2IncidentSeed.from_dict({**valid, "future_field": True})
    with pytest.raises(ValidationError, match="literal_error"):
        Person3IncidentSeed.model_validate({**valid, "schema_version": "2.0"})
    with pytest.raises(ValidationError, match="literal_error"):
        Person1IncidentSeed.model_validate({**valid, "schema_version": "2.0"})
    with pytest.raises(ContractValidationError, match="unsupported_schema_version"):
        Person2IncidentSeed.from_dict({**valid, "schema_version": "2.0"})


def test_timestamps_are_utc_and_naive_values_are_rejected() -> None:
    incident = _incident("bad_db_config")
    offset = timezone(timedelta(hours=5, minutes=30))
    shifted = incident.model_copy(
        update={"detected_at": datetime(2026, 9, 12, 16, 0, tzinfo=offset)}
    )
    normalized = Person3IncidentSeed.model_validate(shifted.model_dump())
    assert normalized.detected_at.utcoffset() == timedelta(0)

    payload = incident.model_dump()
    payload["detected_at"] = datetime(2026, 9, 12, 10, 30)
    with pytest.raises(ValidationError, match="timezone"):
        Person3IncidentSeed.model_validate(payload)


def test_empty_query_plans_require_a_stop_reason_on_both_boundaries() -> None:
    payload = {
        "schema_version": "1.0",
        "incident_id": "inc-1",
        "plan_id": "plan-1",
        "round": 1,
        "queries": [],
    }
    with pytest.raises(ValidationError, match="requires stop_reason"):
        Person1EvidenceQueryPlan.model_validate(payload)
    with pytest.raises(ValidationError, match="requires stop_reason"):
        Person3EvidenceQueryPlan.model_validate(payload)
