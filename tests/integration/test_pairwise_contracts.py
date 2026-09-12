"""Pairwise contract integration tests (WORK_DIVISION §9 and §11.3).

Verifies that Person 1's outputs can be consumed by Person 2 and Person 3
using ONLY the published schemas, and that Person 3's EvidenceQueryPlan can
be consumed by Person 1's CollectionService.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from collectors import (
    CollectionService,
    DefaultCollectionService,
    create_scenario_adapters,
)
from contracts.collection.batch import QueryResult, RawEvidenceBatch, RawRecord
from contracts.collection.capabilities import (
    SourceCapability,
    SourceCapabilityCatalog,
)
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.enums import InformationValue, Severity, SourceStatus, SourceType
from contracts.incident.alert import IncidentAlert
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import FrozenClock
from ingestion.alert.id_gen import DeterministicIdentifierGenerator
from ingestion.alert.ingestor import DefaultAlertIngestor
from ingestion.alert.repository import InMemoryIncidentRepository
from ingestion.capabilities.registry import DefaultSourceRegistry

FIXED_TIME = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)


# ── Person 1 -> Person 2 Integration Contracts ──────────────────────


class TestPerson1ToPerson2Contracts:
    """Person 2 consumes IncidentSeed and RawEvidenceBatch."""

    def test_person_2_consumes_incident_seed_via_schema_only(self):
        """Simulate Person 2 ingesting IncidentSeed using only the published contract."""
        clock = FrozenClock(FIXED_TIME)
        ingestor = DefaultAlertIngestor(clock=clock)
        alert = IncidentAlert(
            external_alert_id="ext-alert-p2-01",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=FIXED_TIME,
            message="High error rate detected in payment pipeline",
            labels={"region": "us-east-1", "tier": "1"},
        )
        seed: IncidentSeed = ingestor.ingest(alert)

        # Person 2 receives the serialized seed over wire / bus / API
        wire_payload = seed.model_dump_json()

        # Person 2 deserializes using only the shared contract
        consumer_seed = IncidentSeed.model_validate_json(wire_payload)

        assert consumer_seed.schema_version == "1.0"
        assert consumer_seed.incident_id.startswith("inc_")
        assert consumer_seed.external_alert_id == "ext-alert-p2-01"
        assert consumer_seed.service == "payment-api"
        assert consumer_seed.environment == "simulation"
        assert consumer_seed.severity == Severity.CRITICAL
        assert consumer_seed.detected_at == FIXED_TIME
        assert consumer_seed.received_at == FIXED_TIME
        assert consumer_seed.summary == "High error rate detected in payment pipeline"
        assert consumer_seed.labels["region"] == "us-east-1"

    def test_person_2_consumes_raw_evidence_batch_via_schema_only(self):
        """Simulate Person 2 consuming RawEvidenceBatch for normalization and timeline."""
        clock = FrozenClock(FIXED_TIME)
        registry = DefaultSourceRegistry(clock=clock)
        adapters = create_scenario_adapters("bad_db_config")
        for a in adapters.values():
            registry.register_source(a)

        service = DefaultCollectionService(registry=registry, clock=clock)
        plan = EvidenceQueryPlan(
            incident_id="inc_p2_test",
            plan_id="plan_p2_01",
            round=1,
            queries=[
                EvidenceQuery(
                    query_id="q_log",
                    source_type=SourceType.LOGS,
                    question="Get error logs",
                    parameters={"limit": 10},
                    expected_information_value=InformationValue.HIGH,
                ),
                EvidenceQuery(
                    query_id="q_chg",
                    source_type=SourceType.CHANGES,
                    question="Get recent commits",
                    parameters={"max_commits": 5},
                    expected_information_value=InformationValue.HIGH,
                ),
            ],
        )

        batch: RawEvidenceBatch = service.execute(plan)

        # Wire transfer
        wire_data = batch.model_dump_json()

        # Person 2 consumes
        consumed_batch = RawEvidenceBatch.model_validate_json(wire_data)

        assert consumed_batch.schema_version == "1.0"
        assert consumed_batch.incident_id == "inc_p2_test"
        assert consumed_batch.plan_id == "plan_p2_01"
        assert len(consumed_batch.results) == 2
        assert consumed_batch.errors == []

        for q_result in consumed_batch.results:
            assert isinstance(q_result.query_id, str)
            assert isinstance(q_result.source_type, SourceType)
            assert q_result.source_status == SourceStatus.OK
            for record in q_result.records:
                # Person 2 needs record ID, event time, content type, and payload
                assert isinstance(record.source_record_id, str)
                assert record.event_time is not None
                assert isinstance(record.content_type, str)
                assert isinstance(record.payload, dict)


# ── Person 1 -> Person 3 Integration Contracts ──────────────────────


class TestPerson1ToPerson3Contracts:
    """Person 3 consumes SourceCapabilityCatalog to plan queries."""

    def test_person_3_consumes_source_capability_catalog_via_schema_only(self):
        """Simulate Person 3 reading SourceCapabilityCatalog to construct EvidenceQueryPlan."""
        clock = FrozenClock(FIXED_TIME)
        registry = DefaultSourceRegistry(clock=clock)
        adapters = create_scenario_adapters("bad_db_config")
        for a in adapters.values():
            registry.register_source(a)

        seed = IncidentSeed(
            incident_id="inc_p3_discovery",
            external_alert_id="alert-001",
            service="payment-api",
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=FIXED_TIME,
            received_at=FIXED_TIME,
            summary="Payment failure",
        )
        catalog: SourceCapabilityCatalog = registry.capabilities(seed)

        # Wire transfer
        wire_catalog = catalog.model_dump_json()

        # Person 3 consumes
        consumed_catalog = SourceCapabilityCatalog.model_validate_json(wire_catalog)

        assert consumed_catalog.schema_version == "1.0"
        assert consumed_catalog.incident_id == "inc_p3_discovery"
        assert len(consumed_catalog.sources) == 6

        # Person 3 checks which sources are available and their limits
        avail_map = {s.source_type: s for s in consumed_catalog.sources if s.available}
        assert set(avail_map.keys()) == set(SourceType)
        assert "limit" in avail_map[SourceType.LOGS].supported_query_fields
        assert avail_map[SourceType.LOGS].maximum_window_seconds == 86400


# ── Person 3 -> Person 1 Integration Contracts ──────────────────────


class TestPerson3ToPerson1Contracts:
    """Person 1 consumes EvidenceQueryPlan produced by Person 3."""

    def test_person_1_consumes_evidence_query_plan_from_person_3(self):
        """Simulate Person 3 sending an EvidenceQueryPlan JSON to Person 1's CollectionService."""
        clock = FrozenClock(FIXED_TIME)
        registry = DefaultSourceRegistry(clock=clock)
        for a in create_scenario_adapters("bad_db_config").values():
            registry.register_source(a)

        service = DefaultCollectionService(registry=registry, clock=clock)

        # Person 3 constructs plan JSON
        plan_json = json.dumps({
            "schema_version": "1.0",
            "incident_id": "inc_p3_to_p1",
            "plan_id": "plan_round_1",
            "round": 1,
            "queries": [
                {
                    "query_id": "qry_person3_01",
                    "source_type": "deployments",
                    "question": "What recent deployment occurred for payment-api?",
                    "parameters": {
                        "service": "payment-api",
                        "limit": 5,
                    },
                    "related_information_ids": ["need_deploy_history"],
                    "discriminates_hypothesis_ids": ["hyp_bad_deploy", "hyp_db_outage"],
                    "expected_information_value": "high",
                }
            ],
            "stop_reason": None,
        })

        # Person 1 receives and deserializes using shared contract
        plan = EvidenceQueryPlan.model_validate_json(plan_json)

        # Person 1 executes plan
        batch = service.execute(plan)

        assert batch.incident_id == "inc_p3_to_p1"
        assert batch.plan_id == "plan_round_1"
        assert len(batch.results) == 1
        assert batch.results[0].query_id == "qry_person3_01"
        assert batch.results[0].source_type == SourceType.DEPLOYMENTS
        assert batch.results[0].source_status == SourceStatus.OK
        assert len(batch.results[0].records) > 0
