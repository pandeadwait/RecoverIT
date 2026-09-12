"""End-to-end fixture workflow tests for Person 1 (WORK_DIVISION §6.8, §6.10, §11.4).

Exercises the entire chain:
    Raw Alert (dict / IncidentAlert)
    → AlertIngestor.ingest() → IncidentSeed
    → SourceRegistry.capabilities(seed) → SourceCapabilityCatalog
    → Hand-written EvidenceQueryPlan
    → CollectionService.execute(plan, catalog) → RawEvidenceBatch

Validated across all five scenario families from ARCHITECTURE §20.4.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from collectors import (
    DefaultCollectionService,
    create_scenario_adapters,
    list_available_scenarios,
)
from contracts.collection.batch import RawEvidenceBatch
from contracts.collection.capabilities import SourceCapabilityCatalog
from contracts.collection.query_plan import EvidenceQuery, EvidenceQueryPlan
from contracts.enums import InformationValue, Severity, SourceStatus, SourceType
from contracts.incident.alert import IncidentAlert
from contracts.incident.seed import IncidentSeed
from ingestion.alert.clock import FrozenClock
from ingestion.alert.id_gen import DeterministicIdentifierGenerator
from ingestion.alert.ingestor import DefaultAlertIngestor
from ingestion.alert.repository import InMemoryIncidentRepository
from ingestion.capabilities.registry import DefaultSourceRegistry

FIXED_T0 = datetime(2026, 9, 12, 10, 30, 0, tzinfo=timezone.utc)
FIXED_T1 = datetime(2026, 9, 12, 10, 30, 2, tzinfo=timezone.utc)
FIXED_T2 = datetime(2026, 9, 12, 10, 31, 0, tzinfo=timezone.utc)


class TestEndToEndWorkflow:
    @pytest.mark.parametrize(
        "scenario_name,service_name",
        [
            ("bad_db_config", "payment-api"),
            ("memory_exhaustion", "order-api"),
            ("dependency_incompatibility", "auth-service"),
            ("real_db_outage", "billing-api"),
            ("coincidental_deployment", "checkout-api"),
        ],
    )
    def test_full_pipeline_per_scenario(
        self, scenario_name: str, service_name: str
    ):
        # 1. Setup determinism abstractions
        clock = FrozenClock(FIXED_T0)
        id_gen = DeterministicIdentifierGenerator(prefix="inc", start=1)
        repo = InMemoryIncidentRepository()

        # 2. Alert Ingestion
        ingestor = DefaultAlertIngestor(
            repository=repo, clock=clock, id_generator=id_gen
        )
        alert = IncidentAlert(
            external_alert_id=f"alert-{scenario_name}-001",
            service=service_name,
            environment="simulation",
            severity=Severity.CRITICAL,
            detected_at=FIXED_T0,
            message=f"Investigation trigger for scenario {scenario_name}",
            labels={"scenario": scenario_name, "cluster": "test-cluster"},
        )
        seed: IncidentSeed = ingestor.ingest(alert)

        assert seed.incident_id == "inc_001"
        assert seed.service == service_name
        assert seed.severity == Severity.CRITICAL

        # 3. Source Capability Discovery
        clock.set_time(FIXED_T1)
        registry = DefaultSourceRegistry(clock=clock)
        scenario_adapters = create_scenario_adapters(scenario_name)
        for adapter in scenario_adapters.values():
            registry.register_source(adapter)

        catalog: SourceCapabilityCatalog = registry.capabilities(seed)

        assert catalog.incident_id == seed.incident_id
        assert len(catalog.sources) == 6
        for source_cap in catalog.sources:
            assert source_cap.available is True
            assert len(source_cap.supported_query_fields) > 0

        # 4. Hand-written EvidenceQueryPlan (simulating Person 3 Round 1)
        param_map = {
            SourceType.LOGS: {"service": service_name, "limit": 20},
            SourceType.METRICS: {"service": service_name},
            SourceType.CHANGES: {"max_commits": 5},
            SourceType.DEPLOYMENTS: {"service": service_name, "limit": 5},
            SourceType.PIPELINES: {"limit": 5},
            SourceType.CONFIGURATION: {"service": service_name},
        }

        queries = [
            EvidenceQuery(
                query_id=f"qry_{st.value}",
                source_type=st,
                question=f"Investigate {st.value} for {service_name}",
                parameters=param_map[st],
                related_information_ids=[f"info_need_{st.value}"],
                discriminates_hypothesis_ids=["hyp_01", "hyp_02"],
                expected_information_value=InformationValue.HIGH,
            )
            for st in SourceType
        ]

        plan = EvidenceQueryPlan(
            incident_id=seed.incident_id,
            plan_id="plan_round_1",
            round=1,
            queries=queries,
        )

        # 5. Collection Service Execution
        clock.set_time(FIXED_T2)
        batch_id_gen = DeterministicIdentifierGenerator(prefix="batch", start=1)
        service = DefaultCollectionService(
            registry=registry,
            clock=clock,
            id_generator=batch_id_gen,
            max_workers=4,
        )

        batch: RawEvidenceBatch = service.execute(plan, catalog)

        # 6. Verify complete output batch
        assert batch.schema_version == "1.0"
        assert batch.incident_id == seed.incident_id
        assert batch.plan_id == "plan_round_1"
        assert batch.batch_id == "batch_001"
        assert batch.collected_at == FIXED_T2
        assert batch.errors == []
        assert len(batch.results) == 6

        # Check every source result in the batch
        by_source = {r.source_type: r for r in batch.results}
        for st in SourceType:
            res = by_source[st]
            assert res.source_status == SourceStatus.OK
            assert len(res.records) > 0
            for rec in res.records:
                assert rec.source_record_id is not None
                assert isinstance(rec.payload, dict)

        # 7. Verify JSON round-trip of full batch
        wire_format = batch.model_dump_json()
        restored_batch = RawEvidenceBatch.model_validate_json(wire_format)
        assert restored_batch == batch
